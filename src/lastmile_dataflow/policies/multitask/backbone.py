from __future__ import annotations
import dataclasses
import logging
import math
import logging
from dataclasses import field
from typing import ClassVar, Optional, Sequence, Tuple, List
import torch
from . import tokenizer
from .config import BaseModelConfig, DataFormatter, MultiModalConfig
from .outputs import OLMoOutput
from .llm import LlmConfig, Llm
from .connector import MolmoVisionBackbone, MolmoVisionBackboneConfig
from .utils import BufferCache, get_default_device

log = logging.getLogger(__name__)


@dataclasses.dataclass
class BackboneConfig(BaseModelConfig):
    _model_name: ClassVar[str] = "video_olmo"

    @classmethod
    def get_default_model_name(cls):
        return "video_olmo"

    data_formatter: DataFormatter = field(default_factory=DataFormatter)
    llm: LlmConfig = field(default_factory=LlmConfig)
    vision_backbone: Optional[MolmoVisionBackboneConfig] = field(
        default_factory=MolmoVisionBackboneConfig
    )
    mm_preprocessor: MultiModalConfig = field(default_factory=MultiModalConfig)
    bi_directional_attn: Optional[str] = None
    shared_low_high_embedding: bool = True

    def build_tokenizer(self):
        return self.llm.build_tokenizer()

    def build_model(self, device=None):
        return MultimodalBackbone(self, device)

    @property
    def max_sequence_length(self):
        return self.llm.max_sequence_length


class MultimodalBackbone(torch.nn.Module):
    def __init__(self, config: BackboneConfig, device=None):
        super().__init__()
        self.config = config
        self.__cache = BufferCache()
        self.transformer: Llm = self.config.llm.build(self.__cache, device)
        self.vision_backbone: Optional[MolmoVisionBackbone] = None
        if self.config.vision_backbone is not None:
            self.vision_backbone = self.config.vision_backbone.build(
                self.config.llm, device
            )
        self.special_ids = tokenizer.get_special_token_ids(
            self.config.build_tokenizer()
        )
        if self.config.bi_directional_attn:
            self.__cache["image_tokens"] = torch.as_tensor(
                [
                    self.special_ids[x]
                    for x in [
                        tokenizer.IMAGE_PATCH_TOKEN,
                        tokenizer.IM_COL_TOKEN,
                        tokenizer.IM_START_TOKEN,
                        tokenizer.LOW_RES_IMAGE_START_TOKEN,
                        tokenizer.FRAME_START_TOKEN,
                        tokenizer.IM_END_TOKEN,
                        tokenizer.FRAME_END_TOKEN,
                        tokenizer.IMAGE_LOW_RES_TOKEN,
                    ]
                ],
                dtype=torch.long,
                device=get_default_device(),
            )
        if self.config.bi_directional_attn == "within_image":
            if self.config.mm_preprocessor.image:
                assert self.config.mm_preprocessor.image.use_single_crop_start_token
            assert self.config.mm_preprocessor.use_frame_special_tokens
        self._low_res_image_start = self.special_ids[
            tokenizer.LOW_RES_IMAGE_START_TOKEN
        ]
        self._frame_end = self.special_ids[tokenizer.FRAME_END_TOKEN]
        self._frame_start = self.special_ids[tokenizer.FRAME_START_TOKEN]
        self._image_end_token_id = self.special_ids[tokenizer.IM_END_TOKEN]
        self._image_start_token_id = self.special_ids[tokenizer.IM_START_TOKEN]
        self._image_low_res_id = self.special_ids[tokenizer.IMAGE_LOW_RES_TOKEN]
        self._image_high_res_id = self.special_ids[tokenizer.IMAGE_PATCH_TOKEN]
        self._image_patch_id = self.special_ids[tokenizer.IMAGE_PATCH_TOKEN]
        self._image_col_token_id = self.special_ids[tokenizer.IM_COL_TOKEN]

    @property
    def device(self) -> torch.device:
        return self.transformer.ln_f.weight.device

    def forward(
        self,
        input_ids: torch.LongTensor,
        input_embeddings: Optional[torch.FloatTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        attention_bias: Optional[torch.Tensor] = None,
        response_mask: Optional[torch.Tensor] = None,
        subsegment_ids: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        labels: Optional[torch.LongTensor] = None,
        loss_masks: Optional[torch.Tensor] = None,
        images: Optional[torch.Tensor] = None,
        image_masks: Optional[torch.Tensor] = None,
        token_pooling: Optional[torch.Tensor] = None,
        low_res_token_pooling: Optional[torch.Tensor] = None,
        response_logits_only=False,
        past_key_values: Optional[Sequence[Tuple[torch.Tensor, torch.Tensor]]] = None,
        use_cache: bool = False,
        last_logits_only: bool = False,
        output_hidden_states: Optional[bool] = None,
        append_last_valid_logits: Optional[torch.Tensor] = None,
        collect_layer_hidden_states: bool = False,
        **kwargs,
    ) -> OLMoOutput:
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else False
        )
        if past_key_values:
            assert len(past_key_values) == self.config.llm.n_layers
        has_image = images is not None
        assert not (has_image and input_embeddings is not None), (
            "Cannot provide both images and input embeddings."
        )
        assert not (has_image and past_key_values is not None), (
            "Cached key and values should not be used with images."
        )
        batch_size, seq_len = (
            input_ids.size()
            if input_embeddings is None
            else input_embeddings.size()[:2]
        )
        dev = input_ids.device
        if past_key_values is None:
            past_length = 0
        else:
            past_length = past_key_values[0][0].size(-2)
        if input_ids is not None:
            if attention_mask is None:
                attention_mask = input_ids != -1
            input_ids = input_ids * (input_ids != -1).to(input_ids.dtype)
            if position_ids is None:
                position_ids = torch.clamp(
                    torch.cumsum(attention_mask.to(torch.int32), dim=-1) - 1, min=0
                ).broadcast_to((batch_size, attention_mask.shape[-1]))
        else:
            assert attention_mask is not None
            assert position_ids is not None
        attention_mask_len = past_length + seq_len
        if len(attention_mask.shape) == 2:
            attention_mask = attention_mask[:, :attention_mask_len]
            attention_mask = attention_mask[:, None, :]
        assert attention_mask.shape[-1] == attention_mask_len
        if (
            "casual_mask" not in self.__cache
            or self.__cache["casual_mask"].shape[-1] < attention_mask_len
        ):
            self.__cache["casual_mask"] = torch.tril(
                torch.ones(
                    attention_mask_len, attention_mask_len, device=dev, dtype=torch.bool
                )
            )[None, :, :]
        casual_mask = self.__cache["casual_mask"].to(dev)[
            :, :attention_mask_len, :attention_mask_len
        ]
        bidir_mask = None
        if self.config.bi_directional_attn == "image_tokens":
            image_tokens = self.__cache["image_tokens"].to(input_ids.device)
            c = torch.any(input_ids[:, :, None] == image_tokens[None, None, :], -1)
            bidir_mask = c[:, :, None] & c[:, None, :]
        elif self.config.bi_directional_attn == "within_image":
            is_frame_start = (input_ids == self._frame_start) | (
                input_ids == self._low_res_image_start
            )
            frame_id = torch.cumsum(is_frame_start, dim=-1)
            same_frame = frame_id[:, None] <= frame_id[:, :, None]
            image_tokens = self.__cache["image_tokens"].to(input_ids.device)
            c = torch.any(input_ids[:, :, None] == image_tokens[None, None, :], -1)
            bidir_mask = c[:, :, None] & c[:, None, :] & same_frame
        elif self.config.bi_directional_attn == "image_to_question":
            if images is not None:
                image_tokens = self.__cache["image_tokens"].to(input_ids.device)
                is_image_token = torch.any(
                    input_ids[:, :, None] == image_tokens[None, None, :], -1
                )
                if use_cache:
                    bidir_mask = is_image_token[:, :, None]
                else:
                    bidir_mask = is_image_token[:, :, None] & ~response_mask[:, None, :]
        elif self.config.bi_directional_attn is not None:
            raise NotImplementedError(self.config.bi_directional_attn)
        if bidir_mask is not None:
            attention_mask = attention_mask & (casual_mask | bidir_mask)
        else:
            attention_mask = attention_mask & casual_mask
        attention_mask = attention_mask.unsqueeze(1)
        if input_embeddings is not None:
            x = input_embeddings
        elif self.config.shared_low_high_embedding:
            x = self.transformer.wte(
                torch.where(
                    input_ids == self._image_low_res_id,
                    self._image_high_res_id,
                    input_ids,
                )
            )
        else:
            x = self.transformer.wte(input_ids)
        if attention_bias is not None:
            attention_bias = torch.where(
                attention_mask, attention_bias, torch.finfo(x.dtype).min
            )
        else:
            attention_bias = torch.where(attention_mask, 0, torch.finfo(x.dtype).min)
        deepstack_image_features: Optional[List[List[torch.Tensor]]] = None
        deepstack_masks: Optional[List[torch.Tensor]] = None
        image_pos_masks: Optional[List[torch.Tensor]] = None
        if images is not None:
            if low_res_token_pooling is None:
                vision_backbone_output = self.vision_backbone(
                    images, image_masks, token_pooling, enable_cp=False
                )
                if isinstance(vision_backbone_output, list):
                    image_features = vision_backbone_output[0]
                    deepstack_image_features = [
                        [features] for features in vision_backbone_output[1:]
                    ]
                    deepstack_masks = [None]
                else:
                    image_features = vision_backbone_output
                is_high_res_patch = input_ids.view(-1) == self._image_high_res_id
                if any(is_high_res_patch):
                    x.view(-1, x.shape[-1])[is_high_res_patch] += image_features
                    if deepstack_image_features is not None:
                        image_pos_masks = [is_high_res_patch]
                else:
                    is_image_patch = input_ids.view(-1) == self._image_patch_id
                    x.view(-1, x.shape[-1])[is_image_patch] += image_features
                    if deepstack_image_features is not None:
                        image_pos_masks = [is_image_patch]
            else:
                all_image_features = self.vision_backbone(
                    images,
                    image_masks,
                    [low_res_token_pooling, token_pooling],
                    enable_cp=False,
                )
                if isinstance(all_image_features[0][0], list):
                    deepstack_image_features = [
                        [None] * 2 for _ in range(len(all_image_features[0][0]) - 1)
                    ]
                    deepstack_masks = [None] * 2
                    image_pos_masks = [None] * 2
                for i, input_id in enumerate(
                    [self._image_low_res_id, self._image_high_res_id]
                ):
                    image_features, mask = all_image_features[i]
                    if isinstance(image_features, list):
                        for j, features in enumerate(image_features[1:]):
                            deepstack_image_features[j][i] = features
                        image_features = image_features[0]
                    is_image_patch = input_ids.view(-1) == input_id
                    if deepstack_image_features is not None:
                        image_pos_masks[i] = is_image_patch
                        deepstack_masks[i] = mask
                    x = x.clone()
                    x.view(-1, x.shape[-1])[is_image_patch] += image_features.view(
                        -1, image_features.shape[-1]
                    )[mask.view(-1)]
        per_block_kwargs = {}
        all_block_kwargs = {}
        if not self.config.llm.rope:
            pos = torch.arange(
                past_length, past_length + seq_len, dtype=torch.long, device=x.device
            ).unsqueeze(0)
            pos_emb = self.transformer.wpe(pos)
            x = pos_emb + x
        x = self.transformer.emb_drop(x)
        if self.config.llm.normalize_input_embeds:
            x = x * self.config.llm.d_model**0.5
        attn_key_values: Optional[List[Tuple[torch.Tensor, torch.Tensor]]] = (
            [] if use_cache else None
        )
        all_hidden_states = []
        block_hidden_states: Optional[List[torch.Tensor]] = (
            [] if collect_layer_hidden_states else None
        )
        for block_idx, block in enumerate(self.transformer.blocks):
            if output_hidden_states:
                all_hidden_states.append(x)
            block_kwargs = per_block_kwargs.get(block_idx, {})
            layer_past = None if past_key_values is None else past_key_values[block_idx]
            x, cache = block(
                x,
                attention_bias=attention_bias,
                position_ids=position_ids,
                drop_mask=response_mask,
                layer_past=layer_past,
                use_cache=use_cache,
                **all_block_kwargs,
                **block_kwargs,
            )
            if deepstack_image_features is not None and block_idx in range(
                len(deepstack_image_features)
            ):
                for i, (is_image_patch, mask) in enumerate(
                    zip(image_pos_masks, deepstack_masks)
                ):
                    image_features = deepstack_image_features[block_idx][i]
                    x = x.clone()
                    if mask is not None:
                        added_features = image_features.view(
                            -1, image_features.shape[-1]
                        )[mask.view(-1)]
                    else:
                        added_features = image_features
                    x.view(-1, x.shape[-1])[is_image_patch] += added_features
            if block_hidden_states is not None:
                block_hidden_states.append(x)
            if attn_key_values is not None:
                assert cache is not None
                attn_key_values.append(cache)
        if last_logits_only:
            if append_last_valid_logits is not None:
                last_valid_output = x[
                    torch.arange(x.shape[0], device=x.device),
                    append_last_valid_logits.to(x.device),
                ]
                x = last_valid_output.unsqueeze(1)
            else:
                x = x[:, -1, :].unsqueeze(1)
        x = self.transformer.ln_f(x)
        if output_hidden_states:
            all_hidden_states.append(x)
        if response_logits_only:
            assert not append_last_valid_logits
            x = x.view(-1, x.shape[-1])[response_mask.view(-1)]
            if self.config.llm.weight_tying:
                logits = self.transformer.wte(x, logits=True)
            else:
                logits = self.transformer.ff_out(x)
            if self.config.llm.scale_logits:
                logits.mul_(1 / math.sqrt(self.config.llm.d_model))
        else:
            if self.config.llm.weight_tying:
                logits = self.transformer.wte(x, logits=True)
            else:
                logits = self.transformer.ff_out(x)
            if self.config.llm.scale_logits:
                logits.mul_(1 / math.sqrt(self.config.llm.d_model))
            if not last_logits_only and append_last_valid_logits is not None:
                last_valid_logit = logits[
                    torch.arange(logits.shape[0], device=logits.device),
                    append_last_valid_logits,
                ]
                logits = torch.cat([logits[:, :-1], last_valid_logit[:, None]], dim=1)
        internal = None
        if block_hidden_states is not None:
            internal = {"layer_hidden_states": tuple(block_hidden_states)}
        return OLMoOutput(
            logits=logits,
            attn_key_values=attn_key_values,
            hidden_states=tuple(all_hidden_states) if output_hidden_states else None,
            internal=internal,
        )
