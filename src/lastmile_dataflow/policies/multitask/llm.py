from __future__ import annotations
import dataclasses
import logging
from abc import abstractmethod
from dataclasses import field
from typing import Callable, Optional, Sequence, Tuple, Union
import torch
import torch.backends.cuda
import torch.nn as nn
import torch.nn.functional as F
from torch import einsum
from .config import BaseConfig, StrEnum
from .tokenizer import TokenizerConfig
from .utils import ensure_finite_, BufferCache

log = logging.getLogger(__name__)


class LayerNormType(StrEnum):
    default = "default"
    low_precision = "low_precision"
    rms = "rms"


class AttentionLayerNormType(StrEnum):
    olmo = "olmo"
    qwen3 = "qwen3"


class ActivationType(StrEnum):
    quick_gelu = "quick_gelu"
    gelu = "gelu"
    gelu_pytorch_tanh = "gelu_pytorch_tanh"
    relu = "relu"
    silu = "silu"
    swiglu = "swiglu"


class AttentionType(StrEnum):
    sdpa = "sdpa"
    direct = "direct"
    flash = "flash"


class BlockType(StrEnum):
    sequential = "sequential"
    llama = "llama"
    moe = "moe"


class RopeType(StrEnum):
    default = "default"
    llama3 = "llama3"
    yarn = "yarn"


@dataclasses.dataclass
class LlmConfig(BaseConfig):
    d_model: int = 768
    n_heads: int = 12
    n_kv_heads: Optional[int] = None
    head_dim: Optional[int] = None
    qkv_bias: bool = False
    clip_qkv: Optional[float] = None
    n_layers: int = 12
    mlp_ratio: int = 4
    mlp_hidden_size: Optional[int] = None
    activation_type: ActivationType = ActivationType.swiglu
    block_type: BlockType = BlockType.sequential
    rope: bool = False
    rope_full_precision: bool = True
    rope_theta: float = 10000.0
    rope_type: RopeType = RopeType.default
    attention_type: AttentionType = AttentionType.sdpa
    full_attention_layers: Optional[Tuple[int]] = None
    sliding_attention_rope_scaling: bool = False
    float32_attention: bool = True
    attention_dropout: float = 0.0
    attention_layer_norm: bool = False
    attention_layer_norm_type: AttentionLayerNormType = AttentionLayerNormType.olmo
    residual_dropout: float = 0.1
    response_residual_dropout: float = 0.0
    layer_norm_type: LayerNormType = LayerNormType.default
    layer_norm_with_affine: bool = True
    layer_norm_eps: Optional[float] = None
    attention_layer_norm_with_affine: bool = True
    max_sequence_length: int = 1024
    max_position_embeddings: Optional[int] = None
    include_bias: bool = True
    bias_for_layer_norm: Optional[bool] = None
    norm_after: bool = False
    embedding_dropout: float = 0.1
    scale_logits: bool = False
    vocab_size: int = 50257
    additional_vocab_size: Optional[int] = 128
    weight_tying: bool = True
    embedding_size: Optional[int] = 50304
    tokenizer: TokenizerConfig = field(default_factory=TokenizerConfig)
    new_embedding_init_range: float = 0.02
    initializer_range: float = 0.02
    normalize_input_embeds: bool = False
    fix_pad_tokenizer: bool = False

    def __post_init__(self):
        if self.full_attention_layers is not None:
            self.full_attention_layers = tuple(self.full_attention_layers)

    def build(self, cache, device=None) -> "Llm":
        return Llm(self, cache, device)

    def build_tokenizer(self):
        if self.fix_pad_tokenizer:
            pad_tokenizer_to = self.embedding_size or self.vocab_size
        else:
            pad_tokenizer_to = self.vocab_size
        return self.tokenizer.build(pad_tokenizer_to=pad_tokenizer_to)

    @property
    def effective_n_kv_heads(self) -> int:
        if self.n_kv_heads is None:
            return self.n_heads
        else:
            return self.n_kv_heads


class Llm(nn.Module):
    def __init__(self, config: LlmConfig, cache, device):
        super().__init__()
        self.config = config
        self.blocks = nn.ModuleList(
            [OLMoBlock.build(i, config, cache, device) for i in range(config.n_layers)]
        )
        self.ln_f = LayerNorm.build(config)
        self.emb_drop = nn.Dropout(config.embedding_dropout)
        if config.additional_vocab_size is not None:
            self.wte = Embedding(
                config.embedding_size or config.vocab_size,
                config.additional_vocab_size,
                config.d_model,
                device=device,
                initializer_range=config.initializer_range,
                new_embed_initializer_range=config.new_embedding_init_range,
            )
        else:
            self.wte = nn.Embedding(
                config.embedding_size or config.vocab_size,
                config.d_model,
                device=device,
            )
        if not config.weight_tying:
            self.ff_out = nn.Linear(
                config.d_model,
                config.embedding_size or config.vocab_size,
                bias=config.include_bias,
                device=device,
            )


class Embedding(nn.Module):
    def __init__(
        self,
        num_embeddings: int,
        num_new_embeddings: int,
        features: int,
        device: Union[str, torch.device],
        initializer_range: float = 0.02,
        new_embed_initializer_range: float = 0.02,
    ):
        super().__init__()
        self.initializer_range = initializer_range
        self.new_embed_initializer_range = new_embed_initializer_range
        self.embedding = nn.Parameter(
            torch.zeros(num_embeddings, features, device=device)
        )
        self.new_embedding = nn.Parameter(
            torch.zeros(num_new_embeddings, features, device=device)
        )

    def forward(self, x: torch.Tensor, logits=False) -> torch.Tensor:
        if logits:
            return F.linear(x, self.embedding, None)
        return F.embedding(x, torch.cat([self.embedding, self.new_embedding], dim=0))


class Dropout(nn.Dropout):
    def __init__(
        self,
        p: float = 0.5,
        inplace: bool = False,
        mask_p: float = 0,
        broadcast_dims: Sequence[int] = (),
    ):
        super().__init__(p, inplace)
        self.mask_p = mask_p
        self.broadcast_dims = broadcast_dims

    def forward(
        self, input: torch.Tensor, drop_mask: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        if self.p == 0.0 and (self.mask_p is None or self.mask_p == 0.0):
            return input
        elif self.mask_p > 0.0 and self.training:
            assert drop_mask is not None
            drop_mask = drop_mask.to(input.dtype)
            keep_prob = 1.0 - self.p
            keep_prob2 = 1.0 - self.mask_p
            keep_prob = drop_mask * keep_prob2 + (1 - drop_mask) * keep_prob
            keep_prob = keep_prob.unsqueeze(-1)
            dropout_shape = list(input.shape)
            keep_prob = keep_prob.broadcast_to(dropout_shape)
            multiplier = input.new_empty(dropout_shape).bernoulli_(keep_prob)
            multiplier.div_(keep_prob)
            return input * multiplier
        elif self.p > 0.0 and len(self.broadcast_dims) > 0 and self.training:
            keep_prob = 1.0 - self.p
            dropout_shape = list(input.shape)
            for dim in self.broadcast_dims:
                dropout_shape[dim] = 1
            keep = input.new_empty(dropout_shape).bernoulli_(keep_prob)
            multiplier = keep.broadcast_to(input.shape)
            multiplier.div_(keep_prob)
            input = input * multiplier
        else:
            return F.dropout(input, self.p, self.training, self.inplace)


class LayerNormBase(nn.Module):
    def __init__(
        self,
        config: LlmConfig,
        *,
        size: Optional[int] = None,
        elementwise_affine: Optional[bool] = True,
        eps: float = 1e-05,
        weight_initializer: Optional[Callable] = torch.ones,
        bias_initializer: Optional[Callable] = torch.zeros,
        device=None,
    ):
        super().__init__()
        self.config = config
        self.eps = self.config.layer_norm_eps or eps
        self.normalized_shape = (size or config.d_model,)
        if elementwise_affine or (
            elementwise_affine is None and self.config.layer_norm_with_affine
        ):
            self.weight = nn.Parameter(
                weight_initializer(self.normalized_shape, device=device)
            )
            use_bias = self.config.bias_for_layer_norm
            if use_bias is None:
                use_bias = self.config.include_bias
            if use_bias:
                self.bias = nn.Parameter(
                    bias_initializer(self.normalized_shape, device=device)
                )
            else:
                self.register_parameter("bias", None)
        else:
            self.register_parameter("bias", None)
            self.register_parameter("weight", None)

    @abstractmethod
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    @classmethod
    def build(
        cls, config: LlmConfig, size: Optional[int] = None, **kwargs
    ) -> LayerNormBase:
        if config.layer_norm_type == LayerNormType.default:
            return LayerNorm(config, size=size, low_precision=False, **kwargs)
        elif config.layer_norm_type == LayerNormType.low_precision:
            return LayerNorm(config, size=size, low_precision=True, **kwargs)
        elif config.layer_norm_type == LayerNormType.rms:
            return RMSLayerNorm(config, size=size, **kwargs)
        else:
            raise NotImplementedError(
                f"Unknown LayerNorm type: '{config.layer_norm_type}'"
            )

    def _cast_if_autocast_enabled(
        self, tensor: torch.Tensor, dtype: Optional[torch.dtype] = None
    ) -> torch.Tensor:
        if tensor.device.type == "cuda" and torch.is_autocast_enabled():
            return tensor.to(
                dtype=dtype if dtype is not None else torch.get_autocast_gpu_dtype()
            )
        elif tensor.device.type == "cpu" and torch.is_autocast_cpu_enabled():
            return tensor.to(
                dtype=dtype if dtype is not None else torch.get_autocast_cpu_dtype()
            )
        else:
            return tensor


class LayerNorm(LayerNormBase):
    def __init__(
        self,
        config: LlmConfig,
        size: Optional[int] = None,
        low_precision: bool = False,
        elementwise_affine: Optional[bool] = None,
        eps: float = 1e-05,
    ):
        super().__init__(
            config, size=size, elementwise_affine=elementwise_affine, eps=eps
        )
        self.low_precision = low_precision

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.low_precision:
            module_device = x.device
            downcast_x = self._cast_if_autocast_enabled(x)
            downcast_weight = (
                self._cast_if_autocast_enabled(self.weight)
                if self.weight is not None
                else self.weight
            )
            downcast_bias = (
                self._cast_if_autocast_enabled(self.bias)
                if self.bias is not None
                else self.bias
            )
            with torch.autocast(enabled=False, device_type=module_device.type):
                return F.layer_norm(
                    downcast_x,
                    self.normalized_shape,
                    weight=downcast_weight,
                    bias=downcast_bias,
                    eps=self.eps,
                )
        else:
            return F.layer_norm(
                x,
                self.normalized_shape,
                weight=self.weight,
                bias=self.bias,
                eps=self.eps,
            )


class RMSLayerNorm(LayerNormBase):
    def __init__(
        self,
        config: LlmConfig,
        size: Optional[int] = None,
        elementwise_affine: Optional[bool] = None,
        eps: float = 1e-05,
    ):
        super().__init__(
            config, size=size, elementwise_affine=elementwise_affine, eps=eps
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        with torch.autocast(enabled=False, device_type=x.device.type):
            og_dtype = x.dtype
            x = x.to(torch.float32)
            variance = x.pow(2).mean(-1, keepdim=True)
            x = x * torch.rsqrt(variance + self.eps)
            x = x.to(og_dtype)
        if self.weight is not None:
            if self.bias is not None:
                return self.weight * x + self.bias
            else:
                return self.weight * x
        else:
            return x


class RotaryEmbedding(nn.Module):
    def __init__(
        self,
        config: LlmConfig,
        cache: BufferCache,
        device=None,
        rope_type: Optional[RopeType] = None,
    ):
        super().__init__()
        self.config = config
        self.__cache = cache
        self.rope_type = rope_type or config.rope_type

    def get_rotary_embedding(
        self, seq_len: int, device: torch.device, rope_type: Optional[RopeType] = None
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        rope_type = rope_type or self.rope_type
        prefix = f"{str(rope_type)}_" if rope_type != RopeType.default else ""
        sin_key = f"{prefix}rope_pos_sin"
        cos_key = f"{prefix}rope_pos_cos"
        if (
            (pos_sin := self.__cache.get(sin_key)) is not None
            and (pos_cos := self.__cache.get(cos_key)) is not None
            and (pos_sin.shape[-2] >= seq_len)
            and (pos_cos.shape[-2] >= seq_len)
        ):
            if pos_sin.device != device:
                pos_sin = pos_sin.to(device)
                if not torch.compiler.is_compiling():
                    self.__cache[sin_key] = pos_sin
            if pos_cos.device != device:
                pos_cos = pos_cos.to(device)
                if not torch.compiler.is_compiling():
                    self.__cache[cos_key] = pos_cos
            return (pos_sin[:, :, :seq_len, :], pos_cos[:, :, :seq_len, :])
        with torch.autocast(device.type, enabled=False):
            dim = (
                self.config.head_dim
                if self.config.head_dim is not None
                else self.config.d_model // self.config.n_heads
            )
            inv_freq = 1.0 / (
                self.config.rope_theta
                ** (torch.arange(0, dim, 2, device=device, dtype=torch.float) / dim)
            )
            attention_factor = 1.0
            seq = torch.arange(seq_len, device=device, dtype=torch.float)
            freqs = einsum("i , j -> i j", seq, inv_freq)
            positions = torch.cat((freqs, freqs), dim=-1)
            pos_sin, pos_cos = (
                positions.sin()[None, None, :, :],
                positions.cos()[None, None, :, :],
            )
            pos_sin, pos_cos = (pos_sin * attention_factor, pos_cos * attention_factor)
        self.__cache[sin_key] = pos_sin
        self.__cache[cos_key] = pos_cos
        return (pos_sin, pos_cos)

    def rotate_half(self, x: torch.Tensor) -> torch.Tensor:
        B, nh, T, hs = x.size()
        x = x.view(B, nh, T, 2, hs // 2)
        x1, x2 = x.unbind(dim=-2)
        return torch.cat((-x2, x1), dim=-1)

    def apply_rotary_pos_emb(
        self, pos_sin: torch.Tensor, pos_cos: torch.Tensor, t: torch.Tensor
    ) -> torch.Tensor:
        return (t * pos_cos + self.rotate_half(t) * pos_sin).to(t.dtype)

    def forward(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        position_ids: Optional[torch.Tensor] = None,
        head_first: bool = True,
        cp_enabled: bool = False,
        pos_sin: Optional[torch.Tensor] = None,
        pos_cos: Optional[torch.Tensor] = None,
        freqs_cis: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        assert pos_sin is None and pos_cos is None and (freqs_cis is None), (
            "'pos_sin', 'pos_cos' and 'freqs_cis' must be None when CP is not enabled"
        )
        if self.config.rope_full_precision:
            q_, k_ = (q.float(), k.float())
        else:
            q_, k_ = (q, k)
        with torch.autocast(q.device.type, enabled=False):
            batch_size = q_.shape[0]
            query_len, key_len = (q_.shape[-2], k_.shape[-2])
            if position_ids is not None:
                freqs_cis_len = (
                    self.config.max_position_embeddings
                    or self.config.max_sequence_length
                )
            else:
                freqs_cis_len = key_len
            pos_sin, pos_cos = self.get_rotary_embedding(freqs_cis_len, q_.device)
            pos_sin = pos_sin.type_as(q_)
            pos_cos = pos_cos.type_as(q_)
            if position_ids is not None:
                assert query_len == key_len, (
                    "Query and key lengths must be equal when using position IDs."
                )
                pos_sin = pos_sin[0, 0][position_ids].view(
                    (batch_size, 1, key_len, pos_sin.shape[-1])
                )
                pos_cos = pos_cos[0, 0][position_ids].view(
                    (batch_size, 1, key_len, pos_cos.shape[-1])
                )
            q_ = self.apply_rotary_pos_emb(
                pos_sin[:, :, key_len - query_len : key_len, :],
                pos_cos[:, :, key_len - query_len : key_len, :],
                q_,
            )
            k_ = self.apply_rotary_pos_emb(pos_sin, pos_cos, k_)
        return (q_.type_as(q), k_.type_as(k))


class Activation(nn.Module):
    @abstractmethod
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    @property
    @abstractmethod
    def output_multiplier(self) -> float:
        raise NotImplementedError

    @classmethod
    def build(cls, activation_type, split_inputs=False) -> Activation:
        if split_inputs:
            if activation_type == ActivationType.swiglu:
                return LlamaSwiGLU()
            else:
                raise NotImplementedError(
                    f"Unknown two-input activation: '{activation_type}'"
                )
        elif activation_type == ActivationType.quick_gelu:
            return QuickGELU()
        elif activation_type == ActivationType.gelu:
            return GELU(approximate="none")
        elif activation_type == ActivationType.gelu_pytorch_tanh:
            return GELU(approximate="tanh")
        elif activation_type == ActivationType.relu:
            return ReLU(inplace=False)
        elif activation_type == ActivationType.silu:
            return SiLU(inplace=False)
        elif activation_type == ActivationType.swiglu:
            return SwiGLU()
        else:
            raise NotImplementedError(f"Unknown activation: '{activation_type}'")


class QuickGELU(Activation):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * torch.sigmoid(1.702 * x)

    @property
    def output_multiplier(self) -> float:
        return 1.0


class GELU(nn.GELU):
    @property
    def output_multiplier(self) -> float:
        return 1.0


class ReLU(nn.ReLU):
    @property
    def output_multiplier(self) -> float:
        return 1.0


class SiLU(nn.SiLU):
    @property
    def output_multiplier(self) -> float:
        return 1.0


class SwiGLU(Activation):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x, gate = x.chunk(2, dim=-1)
        return F.silu(gate) * x

    @property
    def output_multiplier(self) -> float:
        return 0.5


class LlamaSwiGLU(Activation):
    def forward(self, x1: torch.Tensor, x2: torch.Tensor) -> torch.Tensor:
        return F.silu(x1) * x2

    @property
    def output_multiplier(self) -> float:
        return 0.5


def causal_attention_bias(seq_len: int, device: torch.device) -> torch.FloatTensor:
    att_bias = torch.triu(
        torch.ones(seq_len, seq_len, device=device, dtype=torch.float), diagonal=1
    )
    att_bias.masked_fill_(att_bias == 1, torch.finfo(att_bias.dtype).min)
    return att_bias.view(1, 1, seq_len, seq_len)


def get_causal_attention_bias(
    cache: BufferCache, seq_len: int, device: torch.device
) -> torch.Tensor:
    if (
        causal_bias := cache.get("causal_attention_bias")
    ) is not None and causal_bias.shape[-1] >= seq_len:
        if causal_bias.device != device:
            causal_bias = causal_bias.to(device)
            cache["causal_attention_bias"] = causal_bias
        return causal_bias
    with torch.autocast(device.type, enabled=False):
        causal_bias = causal_attention_bias(seq_len, device)
    cache["causal_attention_bias"] = causal_bias
    return causal_bias


class OLMoBlock(nn.Module):
    def __init__(
        self, layer_id: int, config: LlmConfig, cache: BufferCache, device=None
    ):
        super().__init__()
        self.layer_id = layer_id
        self.config = config
        self.hidden_size = (
            config.mlp_hidden_size
            if config.mlp_hidden_size is not None
            else config.mlp_ratio * config.d_model
        )
        self.__cache = cache
        if config.head_dim is None:
            assert config.d_model % config.n_heads == 0
        self.head_dim = (
            config.head_dim
            if config.head_dim is not None
            else config.d_model // config.n_heads
        )
        self.dropout = Dropout(
            config.residual_dropout, mask_p=config.response_residual_dropout
        )
        self.k_norm: Optional[LayerNormBase] = None
        self.q_norm: Optional[LayerNormBase] = None
        if config.attention_layer_norm:
            assert config.effective_n_kv_heads is not None
            k_norm_size = (
                self.head_dim
                if config.attention_layer_norm_type == AttentionLayerNormType.qwen3
                else self.head_dim * config.effective_n_kv_heads
            )
            self.k_norm = LayerNormBase.build(
                config,
                size=k_norm_size,
                elementwise_affine=config.attention_layer_norm_with_affine,
            )
            q_norm_size = (
                self.head_dim
                if config.attention_layer_norm_type == AttentionLayerNormType.qwen3
                else None
            )
            self.q_norm = LayerNormBase.build(
                config,
                size=q_norm_size,
                elementwise_affine=config.attention_layer_norm_with_affine,
            )
        if config.clip_qkv is not None:
            assert config.clip_qkv > 0
        self.act = Activation.build(
            config.activation_type, split_inputs=config.block_type == BlockType.llama
        )
        assert self.act.output_multiplier * self.hidden_size % 1 == 0
        input_dim = (
            config.head_dim * config.n_heads
            if config.head_dim is not None
            else config.d_model
        )
        self.attn_out = nn.Linear(
            input_dim, config.d_model, bias=config.include_bias, device=device
        )
        self.ff_out = nn.Linear(
            int(self.act.output_multiplier * self.hidden_size),
            config.d_model,
            bias=config.include_bias,
            device=device,
        )
        self.ff_out._is_residual = True
        if self.config.rope:
            rope_type: Optional[RopeType] = None
            if config.full_attention_layers is not None:
                if config.sliding_attention_rope_scaling:
                    rope_type = (
                        RopeType.default
                        if layer_id in config.full_attention_layers
                        else config.rope_type
                    )
                else:
                    rope_type = (
                        config.rope_type
                        if layer_id in config.full_attention_layers
                        else RopeType.default
                    )
            self.rotary_emb = RotaryEmbedding(
                config, self.__cache, device, rope_type=rope_type
            )

    @classmethod
    def _cast_attn_bias(
        cls, bias: torch.Tensor, input_dtype: torch.dtype
    ) -> torch.Tensor:
        target_dtype = input_dtype
        if bias.device.type == "cuda" and torch.is_autocast_enabled():
            target_dtype = torch.get_autocast_gpu_dtype()
        elif bias.device.type == "cpu" and torch.is_autocast_cpu_enabled():
            target_dtype = torch.get_autocast_cpu_dtype()
        if bias.dtype != target_dtype:
            bias = bias.to(target_dtype)
            ensure_finite_(bias, check_neg_inf=True, check_pos_inf=False)
        return bias

    def _scaled_dot_product_attention(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        attn_mask: Optional[torch.Tensor] = None,
        drop_mask: Optional[torch.Tensor] = None,
        dropout_p: float = 0.0,
        is_causal: bool = False,
    ) -> torch.Tensor:
        if attn_mask is not None:
            attn_mask = attn_mask.to(q.device)
        assert k.size(1) == v.size(1)
        num_kv_heads = k.size(1)
        num_q_heads = q.size(1)
        if num_q_heads != num_kv_heads:
            assert num_q_heads % num_kv_heads == 0
            k = k.repeat_interleave(
                num_q_heads // num_kv_heads, dim=1, output_size=num_q_heads
            )
            v = v.repeat_interleave(
                num_q_heads // num_kv_heads, dim=1, output_size=num_q_heads
            )
        return F.scaled_dot_product_attention(
            q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal
        )

    def attention(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        attention_bias: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        drop_mask: Optional[torch.Tensor] = None,
        layer_past: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
        use_cache: bool = False,
        value_scaling=None,
    ) -> Tuple[torch.Tensor, Optional[Tuple[torch.Tensor, torch.Tensor]]]:
        B, T, C = q.size()
        dtype = k.dtype
        if (
            self.q_norm is not None
            and self.k_norm is not None
            and (self.config.attention_layer_norm_type != AttentionLayerNormType.qwen3)
        ):
            q = self.q_norm(q).to(dtype=dtype)
            k = self.k_norm(k).to(dtype=dtype)
        q = q.view(B, T, self.config.n_heads, self.head_dim)
        k = k.view(B, T, self.config.effective_n_kv_heads, self.head_dim)
        if (
            self.q_norm is not None
            and self.k_norm is not None
            and (self.config.attention_layer_norm_type == AttentionLayerNormType.qwen3)
        ):
            q = self.q_norm(q).to(dtype=dtype)
            k = self.k_norm(k).to(dtype=dtype)
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.view(B, T, self.config.effective_n_kv_heads, self.head_dim).transpose(
            1, 2
        )
        if self.config.rope:
            q, k = self.rotary_emb(q, k, position_ids=position_ids, cp_enabled=False)
        if value_scaling is not None:
            v = v * value_scaling[:, None, :, None]
        if layer_past is not None:
            past_key, past_value = layer_past
            k = torch.cat((past_key.to(k.device), k), dim=-2)
            v = torch.cat((past_value.to(v.device), v), dim=-2)
        present = (k, v) if use_cache else None
        query_len, key_len = (q.shape[-2], k.shape[-2])
        if attention_bias is not None:
            attention_bias = self._cast_attn_bias(
                attention_bias[:, :, key_len - query_len : key_len, :key_len], dtype
            )
        att = self._scaled_dot_product_attention(
            q,
            k,
            v,
            attn_mask=attention_bias,
            drop_mask=drop_mask,
            dropout_p=0.0 if not self.training else self.config.attention_dropout,
            is_causal=attention_bias is None,
        )
        att = att.transpose(1, 2).contiguous().view(B, T, C)
        return (self.attn_out(att), present)

    @abstractmethod
    def forward(
        self,
        x: torch.Tensor,
        attention_bias: Optional[torch.FloatTensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        drop_mask: Optional[torch.Tensor] = None,
        layer_past: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
        use_cache: bool = False,
    ) -> Tuple[torch.Tensor, Optional[Tuple[torch.Tensor, torch.Tensor]]]:
        raise NotImplementedError

    @classmethod
    def build(
        cls, layer_id: int, config: LlmConfig, cache: BufferCache, device=None
    ) -> OLMoBlock:
        return OLMoSequentialBlock(layer_id, config, cache, device)


class OLMoSequentialBlock(OLMoBlock):
    def __init__(
        self, layer_id: int, config: LlmConfig, cache: BufferCache, device=None
    ):
        super().__init__(layer_id, config, cache, device)
        self.attn_norm = LayerNorm.build(config)
        self.ff_norm = LayerNorm.build(config)
        self.fused_dims = (
            config.n_heads * self.head_dim,
            config.effective_n_kv_heads * self.head_dim,
            config.effective_n_kv_heads * self.head_dim,
        )
        self.att_proj = nn.Linear(
            config.d_model,
            sum(self.fused_dims),
            bias=config.include_bias or config.qkv_bias,
            device=device,
        )
        self.ff_proj = nn.Linear(
            config.d_model, self.hidden_size, bias=config.include_bias, device=device
        )

    def forward(
        self,
        x: torch.Tensor,
        attention_bias: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        drop_mask: Optional[torch.Tensor] = None,
        layer_past: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
        value_scaling: Optional[torch.Tensor] = None,
        use_cache: bool = False,
        pos_sin: Optional[torch.Tensor] = None,
        pos_cos: Optional[torch.Tensor] = None,
        freqs_cis: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Optional[Tuple[torch.Tensor, torch.Tensor]]]:
        if not self.config.norm_after:
            atten_in = self.attn_norm(x)
        else:
            atten_in = x
        qkv = self.att_proj(atten_in)
        if self.config.clip_qkv is not None:
            qkv.clamp_(min=-self.config.clip_qkv, max=self.config.clip_qkv)
        q, k, v = qkv.split(self.fused_dims, dim=-1)
        att, cache = self.attention(
            q,
            k,
            v,
            attention_bias,
            position_ids=position_ids,
            drop_mask=drop_mask,
            layer_past=layer_past,
            use_cache=use_cache,
            value_scaling=value_scaling,
        )
        if self.config.norm_after:
            att = self.attn_norm(att)
        x = x + self.dropout(att, drop_mask=drop_mask)
        og_x = x
        if not self.config.norm_after:
            x = self.ff_norm(x)
        x = self.ff_proj(x)
        x = self.act(x)
        x = self.ff_out(x)
        if self.config.norm_after:
            x = self.ff_norm(x)
        x = self.dropout(x, drop_mask=drop_mask)
        x = og_x + x
        return (x, cache)
