from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple
import torch
import torch.backends.cuda
import torch.nn as nn
import torch.nn.functional as F
from transformers.activations import get_activation
from .config import BaseConfig, StrEnum
from .llm import AttentionType, ActivationType
from torch.nn.attention import SDPBackend, sdpa_kernel

log = logging.getLogger(__name__)


class VisionBackboneType(StrEnum):
    openai = "openai"
    siglip = "siglip"
    dino = "dino"


class SdpaBackendType(StrEnum):
    math = "math"
    efficient = "efficient"
    cudnn = "cudnn"
    flash = "flash"
    all = "all"


@dataclass
class VitConfig(BaseConfig):
    image_model_type: VisionBackboneType = VisionBackboneType.openai
    image_default_input_size: Tuple[int, int] = (336, 336)
    image_patch_size: int = 14
    image_emb_dim: int = 1024
    image_num_heads: int = 16
    image_num_key_value_heads: int = 16
    image_num_layers: int = 24
    image_head_dim: int = 64
    image_mlp_dim: int = 4096
    image_mlp_activations: ActivationType = ActivationType.gelu
    image_num_pos: int = 577
    image_norm_eps: float = 1e-05
    attention_dropout: float = 0.0
    residual_dropout: float = 0.0
    initializer_range: float = 0.02
    float32_attention: bool = True
    attention_type: AttentionType = AttentionType.sdpa
    sdpa_backend: SdpaBackendType = SdpaBackendType.all
    resize_mode: str = "default"
    pad_value: float = 0
    normalize: str = "openai"

    def __post_init__(self):
        self.image_default_input_size = tuple(self.image_default_input_size)

    @property
    def image_num_patch(self):
        h, w = self.image_default_input_size
        return (h // self.image_patch_size, w // self.image_patch_size)

    def build(self, device):
        return SiglipVisionTransformer(self)


def _expand_token(token, batch_size: int):
    return token.view(1, 1, -1).expand(batch_size, -1, -1)


class ViTMultiHeadDotProductAttention(nn.Module):
    def __init__(
        self, config: VitConfig, use_bias: bool = True, input_dim=None, device=None
    ):
        super().__init__()
        self.config = config
        self.use_bias = use_bias
        self.embed_dim = config.image_emb_dim
        self.num_heads = config.image_num_heads
        self.head_dim = config.image_head_dim
        self.num_key_value_heads = config.image_num_key_value_heads
        self.num_key_value_groups = self.num_heads // self.num_key_value_heads
        self.initializer_range = config.initializer_range
        if input_dim is None:
            input_dim = self.embed_dim
        self.wq = nn.Linear(
            input_dim, self.num_heads * self.head_dim, bias=use_bias, device=device
        )
        self.wk = nn.Linear(
            input_dim,
            self.num_key_value_heads * self.head_dim,
            bias=use_bias,
            device=device,
        )
        self.wv = nn.Linear(
            input_dim,
            self.num_key_value_heads * self.head_dim,
            bias=use_bias,
            device=device,
        )
        self.wo = nn.Linear(
            self.num_heads * self.head_dim, self.embed_dim, bias=use_bias, device=device
        )
        self.attention_dropout: Optional[nn.Dropout] = None
        if config.attention_dropout > 0:
            self.attention_dropout = nn.Dropout(config.attention_dropout)
        self.residual_dropout = nn.Dropout(config.residual_dropout)
        sdpa_backend_list = []
        if config.sdpa_backend in [SdpaBackendType.all, SdpaBackendType.flash]:
            sdpa_backend_list.append(SDPBackend.FLASH_ATTENTION)
        if config.sdpa_backend in [SdpaBackendType.all, SdpaBackendType.cudnn]:
            sdpa_backend_list.append(SDPBackend.CUDNN_ATTENTION)
        if config.sdpa_backend in [SdpaBackendType.all, SdpaBackendType.efficient]:
            sdpa_backend_list.append(SDPBackend.EFFICIENT_ATTENTION)
        if config.sdpa_backend in [SdpaBackendType.all, SdpaBackendType.math]:
            sdpa_backend_list.append(SDPBackend.MATH)
        self.sdpa_backend_list = sdpa_backend_list

    def _split_heads(self, hidden_states, num_heads) -> torch.Tensor:
        return hidden_states.reshape(
            hidden_states.shape[:2] + (num_heads, self.head_dim)
        )

    def _merge_heads(self, hidden_states) -> torch.Tensor:
        return hidden_states.reshape(hidden_states.shape[:2] + (self.embed_dim,))

    def forward(
        self,
        inputs_q: torch.Tensor,
        inputs_kv: Optional[torch.Tensor] = None,
        attn_mask=None,
    ) -> torch.Tensor:
        if inputs_kv is not None:
            inputs_k = inputs_kv
            inputs_v = inputs_kv
        else:
            inputs_k = inputs_q
            inputs_v = inputs_q
        xq, xk, xv = (self.wq(inputs_q), self.wk(inputs_k), self.wv(inputs_v))
        xq = self._split_heads(xq, self.num_heads)
        xk = self._split_heads(xk, self.num_key_value_heads)
        xv = self._split_heads(xv, self.num_key_value_heads)
        if self.num_heads != self.num_key_value_heads:
            xk = xk.repeat_interleave(
                self.num_key_value_groups, dim=2, output_size=self.num_heads
            )
            xv = xv.repeat_interleave(
                self.num_key_value_groups, dim=2, output_size=self.num_heads
            )
        og_dtype = xq.dtype
        if self.config.float32_attention:
            xq = xq.to(torch.float)
            xk = xk.to(torch.float)
        if self.config.attention_type == AttentionType.direct:
            assert attn_mask is None
            attn_weights = torch.einsum(
                "...qhd,...khd->...hqk", xq / math.sqrt(xq.size(-1)), xk
            )
            attn_weights = F.softmax(attn_weights, dim=-1, dtype=torch.float32).to(
                xq.dtype
            )
            if self.attention_dropout is not None:
                attn_weights = self.attention_dropout(attn_weights)
            attn_output = torch.einsum(
                "...hqk,...khd->...qhd", attn_weights.to(xv.dtype), xv
            )
        elif self.config.attention_type == AttentionType.sdpa:
            if self.config.float32_attention:
                xv = xv.to(torch.float32)
            if inputs_kv is not None:
                with sdpa_kernel(self.sdpa_backend_list):
                    attn_output = F.scaled_dot_product_attention(
                        xq.transpose(1, 2).contiguous(),
                        xk.transpose(1, 2).contiguous(),
                        xv.transpose(1, 2).contiguous(),
                        attn_mask=attn_mask,
                        is_causal=False,
                        dropout_p=self.config.attention_dropout,
                    ).transpose(1, 2)
            else:
                attn_output = F.scaled_dot_product_attention(
                    xq.transpose(1, 2).contiguous(),
                    xk.transpose(1, 2).contiguous(),
                    xv.transpose(1, 2).contiguous(),
                    attn_mask=attn_mask,
                    is_causal=False,
                    dropout_p=self.config.attention_dropout,
                ).transpose(1, 2)
        else:
            raise NotImplementedError(self.config.attention_type)
        attn_output = attn_output.to(og_dtype)
        attn_output = self._merge_heads(attn_output)
        attn_output = self.wo(attn_output)
        attn_output = self.residual_dropout(attn_output)
        return attn_output


class ViTMLP(nn.Module):
    def __init__(self, config: VitConfig, device=None):
        super().__init__()
        self.config = config
        self.w1 = nn.Linear(
            config.image_emb_dim, config.image_mlp_dim, bias=True, device=device
        )
        self.act = get_activation(config.image_mlp_activations)
        self.w2 = nn.Linear(
            config.image_mlp_dim, config.image_emb_dim, bias=True, device=device
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.w1(x)
        x = self.act(x)
        x = self.w2(x)
        return x


class BlockCollection(nn.Module):
    def __init__(self, config: VitConfig, device=None):
        super().__init__()
        self.config = config
        self.resblocks = nn.ModuleList(
            [
                ResidualAttentionBlock(config, device)
                for _ in range(config.image_num_layers)
            ]
        )

    def forward(self, x: torch.Tensor) -> List[torch.Tensor]:
        hidden_states = []
        for r in self.resblocks:
            x = r(x)
            hidden_states.append(x)
        return hidden_states


class ResidualAttentionBlock(nn.Module):
    def __init__(self, config: VitConfig, device=None):
        super().__init__()
        self.config = config
        self.attention = ViTMultiHeadDotProductAttention(config)
        self.feed_forward = ViTMLP(config)
        self.attention_norm = nn.LayerNorm(
            config.image_emb_dim, eps=config.image_norm_eps, device=device
        )
        self.ffn_norm = nn.LayerNorm(
            config.image_emb_dim, eps=config.image_norm_eps, device=device
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attention(self.attention_norm(x))
        x = x + self.feed_forward(self.ffn_norm(x))
        return x


class SiglipVisionTransformer(nn.Module):
    def __init__(self, config: VitConfig, device=None):
        super().__init__()
        self.config = config
        self.scale = config.image_emb_dim ** (-0.5)
        self.num_prefix_tokens: int = 0
        self.positional_embedding = nn.Parameter(
            torch.zeros(config.image_num_pos, config.image_emb_dim, device=device)
        )
        image_patch_size = config.image_patch_size
        self.patch_embedding = nn.Linear(
            image_patch_size * image_patch_size * 3,
            config.image_emb_dim,
            bias=True,
            device=device,
        )
        self.transformer = BlockCollection(config, device)

    def add_pos_emb(self, x: torch.Tensor, patch_num: int) -> torch.Tensor:
        pos_emb = self.positional_embedding
        pos_emb = pos_emb.reshape(
            (
                int(math.sqrt(pos_emb.shape[0])),
                int(math.sqrt(pos_emb.shape[0])),
                pos_emb.shape[1],
            )
        )
        patch_num_0, patch_num_1 = patch_num
        if pos_emb.shape[0] != patch_num_0 or pos_emb.shape[1] != patch_num_1:
            pos_emb = pos_emb.unsqueeze(0).permute(0, 3, 1, 2)
            pos_emb = F.interpolate(
                pos_emb,
                size=(patch_num_0, patch_num_1),
                mode="bicubic",
                align_corners=False,
                antialias=True,
            )
            pos_emb = pos_emb.permute(0, 2, 3, 1).squeeze(0)
        pos_emb = pos_emb.reshape(-1, pos_emb.shape[-1])
        x = x + pos_emb[None, :, :].to(x.dtype)
        return x

    def forward(self, x: torch.Tensor, patch_num: int = None) -> List[torch.Tensor]:
        if patch_num is None:
            patch_num = self.config.image_num_patch
        B, N, D = x.shape
        x = self.patch_embedding(x)
        x = self.add_pos_emb(x, patch_num)
        hidden_states = self.transformer(x)
        return hidden_states
