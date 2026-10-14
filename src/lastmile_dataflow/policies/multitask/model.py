from __future__ import annotations
import dataclasses
from dataclasses import field
from typing import Optional, Sequence, Tuple
import torch
from .outputs import OLMoOutput
from .backbone import MultimodalBackbone, BackboneConfig
from .action_expert import ActionExpert, ActionExpertConfig
from .normalization import RobotProcessorConfig


@dataclasses.dataclass
class MolmoBotConfig(BackboneConfig):
    action_dim: int = 7
    action_horizon: int = 16
    action_expert: ActionExpertConfig = field(default_factory=ActionExpertConfig)
    action_expert_layer_mode: str = "per_layer"
    flow_matching_num_steps: int = 10
    states_mode: str = "cross_attn"
    robot_preprocessor: Optional[RobotProcessorConfig] = None
    robot_postprocessor: Optional[RobotProcessorConfig] = None

    def build_model(self, device=None):
        return MolmoBot(self, device)


class MolmoBot(MultimodalBackbone):
    def __init__(self, config: MolmoBotConfig, device=None):
        super().__init__(config, device)
        valid_modes = {"per_layer", "even", "last", "mean"}
        if config.action_expert_layer_mode not in valid_modes:
            raise ValueError(
                f"Unknown action_expert_layer_mode '{config.action_expert_layer_mode}'. Expected one of {sorted(valid_modes)}."
            )
        if config.action_expert.action_dim != config.action_dim:
            config.action_expert.action_dim = config.action_dim
        if config.action_expert.max_horizon < config.action_horizon:
            config.action_expert.max_horizon = config.action_horizon
        if (
            config.action_expert_layer_mode == "per_layer"
            and config.action_expert.num_layers != self.config.llm.n_layers
        ):
            raise ValueError(
                f"Action expert depth ({config.action_expert.num_layers}) must match LLM layers ({self.config.llm.n_layers}) when using per_layer conditioning."
            )
        self.action_expert: ActionExpert = config.action_expert.build(
            llm_dim=self.config.llm.d_model, device=device
        )

    def adapt_state_based_on_mode(self, states: Optional[torch.Tensor] = None):
        if self.config.states_mode == "skip":
            states = None
        elif self.config.states_mode == "zero":
            if states is not None:
                states = torch.zeros_like(states)
        elif self.config.states_mode in ["cross_attn", "self_attn"]:
            pass
        elif self.config.states_mode == "set_arm_zero":
            if states is not None:
                states = states.clone()
                states[:, :7] = 0
        elif self.config.states_mode == "set_gripper_zero":
            if states is not None:
                states = states.clone()
                states[:, -1:] = 0
        else:
            raise ValueError(f"Unknown states_mode: {self.config.states_mode}")
        return states

    @torch.no_grad()
    def generate_actions(
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
        response_logits_only: bool = False,
        past_key_values: Optional[Sequence[Tuple[torch.Tensor, torch.Tensor]]] = None,
        use_cache: bool = False,
        last_logits_only: bool = False,
        append_last_valid_logits: Optional[torch.Tensor] = None,
        states: Optional[torch.Tensor] = None,
        num_steps: Optional[int] = None,
        generator: Optional[torch.Generator] = None,
    ) -> torch.Tensor:
        if states is None:
            raise ValueError("States must be provided for action generation")
        encoder_attention_mask = self._get_encoder_attention_mask(
            input_ids, attention_mask
        )
        forward_kwargs = dict(
            input_ids=input_ids,
            input_embeddings=input_embeddings,
            attention_mask=attention_mask,
            attention_bias=attention_bias,
            response_mask=response_mask,
            subsegment_ids=subsegment_ids,
            position_ids=position_ids,
            labels=labels,
            loss_masks=loss_masks,
            images=images,
            image_masks=image_masks,
            token_pooling=token_pooling,
            low_res_token_pooling=low_res_token_pooling,
            response_logits_only=response_logits_only,
            past_key_values=past_key_values,
            use_cache=use_cache,
            last_logits_only=last_logits_only,
            append_last_valid_logits=append_last_valid_logits,
        )
        _, layer_states = self._run_backbone(
            collect_layer_hidden_states=True,
            output_hidden_states=False,
            **forward_kwargs,
        )
        if layer_states is None:
            raise RuntimeError("Failed to capture hidden states for action generation.")
        layer_states = self._select_layer_states(layer_states)
        states = self.adapt_state_based_on_mode(states)
        steps = num_steps or self.config.flow_matching_num_steps
        batch_size = layer_states[0].shape[0]
        device = layer_states[0].device
        trajectory = torch.randn(
            (batch_size, self.config.action_horizon, self.config.action_dim),
            device=device,
            generator=generator,
        )
        dt = 1.0 / steps
        for i in range(steps):
            t = torch.full((batch_size,), i / steps, device=device)
            velocity = self.action_expert(
                trajectory,
                t,
                layer_states,
                encoder_attention_mask=encoder_attention_mask,
                state_embeddings=states,
                states_mode=self.config.states_mode,
            )
            trajectory = trajectory + dt * velocity
        return trajectory

    def _run_backbone(
        self,
        output_hidden_states: bool,
        collect_layer_hidden_states: bool,
        **forward_kwargs,
    ) -> Tuple[OLMoOutput, Optional[Sequence[torch.Tensor]]]:
        kwargs = dict(forward_kwargs)
        kwargs["collect_layer_hidden_states"] = collect_layer_hidden_states
        kwargs["output_hidden_states"] = output_hidden_states
        base_output = super().forward(**kwargs)
        internal = dict(base_output.internal or {})
        layer_states = internal.pop("layer_hidden_states", None)
        if not output_hidden_states:
            base_output = base_output._replace(hidden_states=None)
        base_output = base_output._replace(internal=internal)
        return (base_output, layer_states)

    def _get_encoder_attention_mask(
        self, input_ids: Optional[torch.Tensor], attention_mask: Optional[torch.Tensor]
    ) -> Optional[torch.Tensor]:
        if attention_mask is not None:
            return attention_mask.to(dtype=torch.bool).clone()
        if input_ids is not None:
            return input_ids != -1
        return None

    def _select_layer_states(
        self, layer_states: Sequence[torch.Tensor]
    ) -> Sequence[torch.Tensor]:
        if not layer_states:
            raise ValueError("No layer states provided for action expert conditioning.")
        mode = self.config.action_expert_layer_mode
        num_target = len(self.action_expert.blocks)
        num_available = len(layer_states)
        if mode == "per_layer":
            if num_available != num_target:
                raise ValueError(
                    f"Expected {num_target} layer states, received {num_available} with per_layer mode."
                )
            return layer_states
        if mode == "last":
            return [layer_states[-1]] * num_target
        if mode == "mean":
            stacked = torch.stack(list(layer_states), dim=0)
            mean_state = stacked.mean(dim=0)
            return [mean_state] * num_target
        if mode == "even":
            if num_target <= 0:
                raise ValueError(
                    "Action expert must have at least one block for even layer selection."
                )
            if num_available == 1:
                return [layer_states[0]] * num_target
            if num_target == 1:
                return [layer_states[-1]]
            scale = (num_available - 1) / float(num_target - 1)
            indices = [int(round(i * scale)) for i in range(num_target)]
            return [layer_states[i] for i in indices]
        raise ValueError(f"Unsupported action_expert_layer_mode '{mode}'.")
