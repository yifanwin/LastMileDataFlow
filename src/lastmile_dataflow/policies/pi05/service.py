"""Project-owned π0.5 process; inference only, no simulation dependencies."""

import logging

import jax
import numpy as np

from ..serving import serve
from .config import IMAGE_KEYS, load_config
from .inference import Pi05Inference


class Pi05Runner:
    def __init__(self, path):
        self.config = load_config(path)
        if jax.devices()[0].platform != "gpu":
            raise RuntimeError("π0.5 service requires a visible GPU")
        self.model = None

    def load(self):
        self.model = Pi05Inference(self.config)
        # Compile once during startup, covered by the model load timeout.
        self.model.infer(
            {
                "state": np.zeros(self.config.state_size, np.float32),
                "images": {key: np.zeros((224, 224, 3), np.uint8) for key in IMAGE_KEYS},
                "prompt": "Pick up the cup.",
            }
        )
        return {
            "device": str(jax.devices()[0]),
            "embodiment": self.config.embodiment,
            "normalization": self.config.normalization,
        }

    def reset(self, seed):
        self.model.reset(seed)

    def infer(self, request):
        return self.model.infer(request["observation"])


def main():
    logging.basicConfig(level=logging.INFO)
    serve(Pi05Runner, load_before_accept=True)


if __name__ == "__main__":
    main()
