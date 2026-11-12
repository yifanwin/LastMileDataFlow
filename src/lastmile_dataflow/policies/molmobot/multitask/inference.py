"""Local RBY1Multitask checkpoint -> unnormalized 16 x 20 action chunk."""

from pathlib import Path
import numpy as np
import torch
from .config import load_model_config
from .preprocessing import InferencePreprocessor, collate_observation


class SynthManipMolmoInferenceWrapper:
    def __init__(self, checkpoint_path, device="cuda", tokenizer_path=None):
        self.device = torch.device(device)
        self.model_config = load_model_config(checkpoint_path, tokenizer_path)
        with torch.device("meta"):
            self.model = self.model_config.build_model()
        self.model.to(torch.bfloat16).to_empty(device=self.device)
        state = torch.load(
            Path(checkpoint_path) / "model.pt", map_location="cpu", weights_only=True
        )
        self.model.load_state_dict(state, strict=True, assign=True)
        del state
        self.model.to(self.device, dtype=torch.bfloat16).eval()
        self.preprocessor = InferencePreprocessor(self.model_config)
        self.state_preprocessor = (
            self.model_config.robot_preprocessor.build_preprocessor()
        )
        self.action_postprocessor = (
            self.model_config.robot_postprocessor.build_postprocessor()
        )

    def _prepare_state(self, state):
        return self.state_preprocessor.normalize_state(
            np.asarray(state, dtype=np.float32).reshape(1, 22), "synthmanip"
        )

    @torch.inference_mode()
    def get_action_chunk(self, images, task_description, state, generator=None):
        processed = self.preprocessor(
            {
                "question": task_description,
                "image": images,
                "state": self._prepare_state(state),
            }
        )
        inputs = collate_observation(processed, self.device)
        with torch.autocast(device_type=self.device.type, dtype=torch.bfloat16):
            actions = self.model.generate_actions(**inputs, generator=generator)
        return self.action_postprocessor.unnormalize_action(
            actions.float().cpu().numpy(), "synthmanip"
        )[0]
