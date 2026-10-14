from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence
import torch
import torch.nn.functional as F
from torch import nn
from .config import BaseConfig


def _modulate(
    x: torch.Tensor, shift: torch.Tensor, scale: torch.Tensor
) -> torch.Tensor:
    """Apply AdaLN-style shift/scale modulation."""
    return x * (1 + scale.unsqueeze(1)) + shift.unsqueeze(1)


class ActionExpertAttention(nn.Module):
    """Multi-head attention with optional cross-attention source."""

    def __init__(
        self,
        hidden_size: int,
        num_heads: int,
        attn_dropout: float = 0.0,
        proj_dropout: float = 0.0,
    ) -> None:
        super().__init__()
        assert hidden_size % num_heads == 0, (
            "hidden size must be divisible by num heads"
        )
        self.hidden_size = hidden_size
        self.num_heads = num_heads
        self.head_dim = hidden_size // num_heads
        self.scale = self.head_dim ** (-0.5)
        self.q_proj = nn.Linear(hidden_size, hidden_size, bias=True)
        self.kv_proj = nn.Linear(hidden_size, hidden_size * 2, bias=True)
        self.attn_drop = nn.Dropout(attn_dropout)
        self.proj = nn.Linear(hidden_size, hidden_size)
        self.proj_drop = nn.Dropout(proj_dropout)

    def forward(
        self,
        x: torch.Tensor,
        kv: Optional[torch.Tensor] = None,
        attn_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        if kv is None:
            kv = x
        bsz, tgt_len, _ = x.shape
        src_len = kv.shape[1]
        q = self.q_proj(x)
        kv = self.kv_proj(kv)
        q = q.view(bsz, tgt_len, self.num_heads, self.head_dim).permute(0, 2, 1, 3)
        kv = kv.view(bsz, src_len, 2, self.num_heads, self.head_dim).permute(
            2, 0, 3, 1, 4
        )
        k, v = (kv[0], kv[1])
        q = q * self.scale
        attn_scores = torch.matmul(q, k.transpose(-2, -1))
        if attn_mask is not None:
            attn_scores = attn_scores + attn_mask
        attn = torch.softmax(attn_scores, dim=-1)
        attn = self.attn_drop(attn)
        out = torch.matmul(attn, v)
        out = out.transpose(1, 2).contiguous().view(bsz, tgt_len, self.hidden_size)
        out = self.proj(out)
        out = self.proj_drop(out)
        return out


class ActionExpertMLP(nn.Module):
    """Feed-forward block used inside the ActionExpert transformer."""

    def __init__(self, hidden_size: int, mlp_ratio: float, dropout: float = 0.0):
        super().__init__()
        inner_dim = int(hidden_size * mlp_ratio)
        self.fc1 = nn.Linear(hidden_size, inner_dim)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(inner_dim, hidden_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.fc1(x)
        x = self.act(x)
        x = self.dropout(x)
        x = self.fc2(x)
        x = self.dropout(x)
        return x


class ActionExpertBlock(nn.Module):
    """A single DiT-style block with AdaLN-Zero modulation."""

    def __init__(
        self,
        hidden_size: int,
        num_heads: int,
        mlp_ratio: float,
        attn_dropout: float = 0.0,
        dropout: float = 0.0,
    ):
        super().__init__()
        self.norm1 = nn.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-06)
        self.attn1 = ActionExpertAttention(
            hidden_size, num_heads, attn_dropout=attn_dropout, proj_dropout=dropout
        )
        self.norm2 = nn.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-06)
        self.attn2 = ActionExpertAttention(
            hidden_size, num_heads, attn_dropout=attn_dropout, proj_dropout=dropout
        )
        self.norm3 = nn.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-06)
        self.mlp = ActionExpertMLP(hidden_size, mlp_ratio, dropout=dropout)
        self.adaLN_modulation = nn.Sequential(
            nn.SiLU(), nn.Linear(hidden_size, 9 * hidden_size, bias=True)
        )

    def forward(
        self,
        x: torch.Tensor,
        timestep_embed: torch.Tensor,
        cross_context: torch.Tensor,
        attn_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        (
            shift_msa,
            scale_msa,
            gate_msa,
            shift_mca,
            scale_mca,
            gate_mca,
            shift_mlp,
            scale_mlp,
            gate_mlp,
        ) = self.adaLN_modulation(timestep_embed).chunk(9, dim=1)
        x = x + gate_msa.unsqueeze(1) * self.attn1(
            _modulate(self.norm1(x), shift_msa, scale_msa)
        )
        x = x + gate_mca.unsqueeze(1) * self.attn2(
            _modulate(self.norm2(x), shift_mca, scale_mca),
            kv=cross_context,
            attn_mask=attn_mask,
        )
        x = x + gate_mlp.unsqueeze(1) * self.mlp(
            _modulate(self.norm3(x), shift_mlp, scale_mlp)
        )
        return x


class ActionExpertFinalLayer(nn.Module):
    """Final projection with AdaLN modulation."""

    def __init__(self, hidden_size: int, output_dim: int):
        super().__init__()
        self.norm = nn.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-06)
        self.linear = nn.Linear(hidden_size, output_dim, bias=True)
        self.adaLN = nn.Sequential(
            nn.SiLU(), nn.Linear(hidden_size, 2 * hidden_size, bias=True)
        )

    def forward(self, x: torch.Tensor, timestep_embed: torch.Tensor) -> torch.Tensor:
        shift, scale = self.adaLN(timestep_embed).chunk(2, dim=1)
        x = _modulate(self.norm(x), shift, scale)
        x = self.linear(x)
        return x


class SinusoidalTimeEmbedding(nn.Module):
    """Standard sinusoidal embedding for continuous diffusion timesteps."""

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def forward(self, timesteps: torch.Tensor) -> torch.Tensor:
        if timesteps.dim() > 1:
            timesteps = timesteps.view(timesteps.shape[0], -1)[:, 0]
        device = timesteps.device
        half_dim = self.dim // 2
        freq = torch.exp(
            torch.arange(half_dim, device=device, dtype=timesteps.dtype)
            * (-math.log(10000.0) / max(half_dim - 1, 1))
        )
        args = timesteps[:, None] * freq[None, :]
        emb = torch.cat([torch.sin(args), torch.cos(args)], dim=-1)
        if self.dim % 2 == 1:
            emb = F.pad(emb, (0, 1))
        return emb


@dataclass
class ActionExpertConfig(BaseConfig):
    """Configuration for the action expert DiT module."""

    max_horizon: int = 32
    "Maximum sequence length for the action tokens."
    action_dim: int = 14
    "Dimensionality of each action vector."
    hidden_size: int = 1024
    "Transformer hidden size."
    num_layers: int = 32
    "Number of transformer blocks."
    num_heads: int = 16
    "Number of attention heads."
    mlp_ratio: float = 4.0
    "Width multiplier for the feed-forward layer."
    timestep_embed_dim: int = 256
    "Size of the sinusoidal timestep embedding."
    dropout: float = 0.0
    "Dropout rate inside the attention and MLP projections."
    attn_dropout: float = 0.0
    "Dropout applied to attention weights."
    context_layer_norm: bool = True
    "Whether to normalize projected context embeddings."

    def build(self, llm_dim: int, device=None) -> "ActionExpert":
        return ActionExpert(self, llm_dim=llm_dim, device=device)


class ActionExpert(nn.Module):
    """DiT-style transformer that predicts action trajectories conditioned on VLM features."""

    def __init__(self, config: ActionExpertConfig, llm_dim: int, device=None):
        super().__init__()
        self.config = config
        self.hidden_size = config.hidden_size
        self.action_embed = nn.Linear(
            config.action_dim, config.hidden_size, device=device
        )
        self.action_pos_embed = nn.Parameter(
            torch.zeros(1, config.max_horizon, config.hidden_size, device=device)
        )
        nn.init.trunc_normal_(self.action_pos_embed, std=0.02)
        self.time_embed = nn.Sequential(
            SinusoidalTimeEmbedding(config.timestep_embed_dim),
            nn.Linear(config.timestep_embed_dim, config.hidden_size, device=device),
            nn.SiLU(),
            nn.Linear(config.hidden_size, config.hidden_size, device=device),
        )
        self.context_proj = nn.Linear(
            llm_dim, config.hidden_size, bias=False, device=device
        )
        self.context_norm = (
            nn.LayerNorm(config.hidden_size, elementwise_affine=False)
            if config.context_layer_norm
            else nn.Identity()
        )
        self.state_encoder = nn.Linear(
            config.hidden_size, config.hidden_size, device=device
        )
        self.state_norm = nn.LayerNorm(
            config.hidden_size, elementwise_affine=False, eps=1e-06
        )
        self.blocks = nn.ModuleList(
            [
                ActionExpertBlock(
                    config.hidden_size,
                    config.num_heads,
                    config.mlp_ratio,
                    attn_dropout=config.attn_dropout,
                    dropout=config.dropout,
                )
                for _ in range(config.num_layers)
            ]
        )
        self.final_layer = ActionExpertFinalLayer(config.hidden_size, config.action_dim)

    def reset_parameters(self):
        nn.init.trunc_normal_(self.action_pos_embed, std=0.02)
        self.action_embed.reset_parameters()
        self.context_proj.reset_parameters()
        for module in self.time_embed.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        if isinstance(self.context_norm, nn.LayerNorm):
            self.context_norm.reset_parameters()
        self.state_encoder.reset_parameters()
        self.state_norm.reset_parameters()
        for block in self.blocks:
            for module in block.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)
        for module in self.final_layer.adaLN.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        self.final_layer.linear.reset_parameters()

    def apply_compile(self, **compile_kwargs):
        """Compile the action expert using torch.compile."""
        self.compile(**compile_kwargs)

    def _encode_states(self, states: Optional[torch.Tensor]) -> Optional[torch.Tensor]:
        if states is None:
            return None
        if states.dim() == 2:
            states = states.unsqueeze(1)
        if states.shape[-1] != self.hidden_size:
            feat_dim = states.shape[-1]
            if feat_dim < self.hidden_size:
                pad = self.hidden_size - feat_dim
                states = F.pad(states, (0, pad))
            else:
                states = states[..., : self.hidden_size]
        encoded = self.state_encoder(states)
        return self.state_norm(encoded)

    def _prepare_context(
        self,
        encoder_hidden_states: Sequence[torch.Tensor],
        encoded_states: Optional[torch.Tensor],
    ) -> Sequence[torch.Tensor]:
        contexts = []
        for hidden in encoder_hidden_states:
            ctx = self.context_proj(hidden)
            ctx = self.context_norm(ctx)
            if encoded_states is not None:
                ctx = torch.cat([ctx, encoded_states], dim=1)
            contexts.append(ctx)
        return contexts

    def _build_cross_attention_mask(
        self,
        encoder_attention_mask: Optional[torch.Tensor],
        encoded_states: Optional[torch.Tensor],
        batch_size: int,
        dtype: torch.dtype,
    ) -> Optional[torch.Tensor]:
        state_seq_len = 0 if encoded_states is None else encoded_states.shape[1]
        if encoder_attention_mask is None:
            return None
        if encoder_attention_mask.dim() == 2:
            mask = encoder_attention_mask[:, None, None, :].to(dtype=dtype)
        else:
            mask = encoder_attention_mask.to(dtype=dtype)
        if state_seq_len > 0:
            ones = torch.ones(
                batch_size, 1, 1, state_seq_len, device=mask.device, dtype=mask.dtype
            )
            mask = torch.cat([mask, ones], dim=-1)
        return (1.0 - mask) * torch.finfo(dtype).min

    def forward(
        self,
        actions: torch.Tensor,
        timesteps: torch.Tensor,
        encoder_hidden_states: Sequence[torch.Tensor],
        encoder_attention_mask: Optional[torch.Tensor] = None,
        state_embeddings: Optional[torch.Tensor] = None,
        states_mode: str = "cross_attn",
    ) -> torch.Tensor:
        bsz, seq_len, _ = actions.shape
        if seq_len > self.config.max_horizon:
            raise ValueError(
                f"Action sequence length {seq_len} exceeds configured max_horizon={self.config.max_horizon}"
            )
        if len(encoder_hidden_states) != len(self.blocks):
            raise ValueError(
                f"Expected {len(self.blocks)} encoder hidden states, got {len(encoder_hidden_states)}"
            )
        timestep_embed = self.time_embed(timesteps)
        x = self.action_embed(actions)
        encoded_states = self._encode_states(state_embeddings)
        if states_mode == "self_attn":
            assert encoded_states is not None, (
                "State embeddings must be provided when states_mode is 'self_attn'"
            )
            x = torch.cat([encoded_states, x], dim=1)
            state_seq_len = encoded_states.shape[1]
            total_seq_len = state_seq_len + seq_len
            if total_seq_len > self.config.max_horizon:
                raise ValueError(
                    f"Total sequence length {total_seq_len} (states: {state_seq_len} + actions: {seq_len}) exceeds configured max_horizon={self.config.max_horizon} in self_attn mode"
                )
            pos = self.action_pos_embed[:, :total_seq_len, :]
            x = x + pos
            contexts = self._prepare_context(encoder_hidden_states, None)
            cross_mask = self._build_cross_attention_mask(
                encoder_attention_mask, None, bsz, x.dtype
            )
        else:
            pos = self.action_pos_embed[:, :seq_len, :]
            x = x + pos
            contexts = self._prepare_context(encoder_hidden_states, encoded_states)
            cross_mask = self._build_cross_attention_mask(
                encoder_attention_mask, encoded_states, bsz, x.dtype
            )
        for block, context in zip(self.blocks, contexts):
            x = block(x, timestep_embed, context, attn_mask=cross_mask)
        output = self.final_layer(x, timestep_embed)
        if states_mode == "self_attn":
            state_seq_len = encoded_states.shape[1]
            output = output[:, state_seq_len:, :]
        return output
