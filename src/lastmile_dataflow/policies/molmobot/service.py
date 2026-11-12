"""Private local IPC model process. Uses the repository-local multitask inference backend."""

import logging
import random
import time

import numpy as np
import torch

from lastmile_dataflow.utils.logging_config import setup_logging
from .client import load_molmobot_config
from .multitask.inference import SynthManipMolmoInferenceWrapper
from .multitask.policy import (
    MolmoBotRBY1DoorPlusOpenPolicyConfig,
    MolmoBotRBY1MultitaskPolicy,
    MolmoBotRBY1PickPnPPolicyConfig,
)
from ..serving import InvalidModelChunk, serve

logger = logging.getLogger(__name__)
OPERATIONS = (("pick", MolmoBotRBY1PickPnPPolicyConfig), ("open", MolmoBotRBY1DoorPlusOpenPolicyConfig))


class TraceAgent:
    def __init__(self, agent):
        self.agent = agent
        self.raw = None
        self.images = None
        self.calls = 0
        self.state = None
        self.prompt = None
        self.require_point = False

    def get_action_chunk(self, **kwargs):
        if kwargs["state"].shape != (22,):
            raise ValueError("expected actual 22D robot state")
        self.images = kwargs["images"]
        self.state = kwargs["state"].copy()
        self.prompt = kwargs["task_description"]
        if self.require_point and "<points coords=" not in self.prompt:
            raise ValueError("open_conditioning_point_outside_fisheye_crop")
        started = time.monotonic()
        logger.debug("Generating model action chunk")
        self.raw = self.agent.get_action_chunk(**kwargs)
        logger.debug("Generated model chunk in %.2f s", time.monotonic() - started)
        if self.raw.shape != (16, 20) or not np.isfinite(self.raw).all():
            logger.error("Rejected original model chunk: %r", self.raw)
            raise InvalidModelChunk("invalid_model_action_chunk")
        self.calls += 1
        return self.raw


class MolmoBotRunner:
    def __init__(self, path):
        self.config = load_molmobot_config(path)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; do not silently run a CPU VLA")
        self.trace = None
        self.policies = {}

    def load(self):
        config = self.config
        logger.info("Loading selected checkpoint %s", config.checkpoint, extra={"progress": True})
        self.trace = TraceAgent(
            SynthManipMolmoInferenceWrapper(checkpoint_path=config.checkpoint, tokenizer_path=config.tokenizer_path)
        )
        values = {}
        if config.gripper_mode != "model_default":
            values["clamp_gripper"] = config.gripper_mode == "binary"
        for operation, cls in OPERATIONS:
            policy = MolmoBotRBY1MultitaskPolicy(
                cls(checkpoint_path=config.checkpoint, img_resolution=(1024, 576), **values)
            )
            policy.agent = self.trace
            self.policies[operation] = policy
        logger.info("Model ready, allocated %.1f MiB", torch.cuda.memory_allocated() / 1024**2)
        return {"device": torch.cuda.get_device_name(), "cuda_peak_bytes": torch.cuda.max_memory_allocated()}

    def reset(self, seed):
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        for policy in self.policies.values():
            policy.reset()

    def infer(self, request):
        trace = self.trace
        policy = self.policies[request["operation"]]
        trace.require_point = policy.use_point_prompts
        calls, started = trace.calls, time.monotonic()
        action = policy.inference_model(request["observation"])
        index = policy.buffer_index - 1
        first = request["first"]
        return {
            "action": np.concatenate([action[group] for group in policy.config.action_move_group_names]),
            "raw_action": trace.raw[index].copy(),
            "inference_s": time.monotonic() - started,
            "new_chunk": trace.calls != calls,
            "chunk_index": index,
            "chunk_serial": trace.calls,
            "gripper_binary": policy.clamp_gripper,
            "processed_images": trace.images if first else None,
            "conditioning_points": policy._conditioning_points if first else None,
            "model_chunk_state": trace.state.copy(),
            "model_prompt": trace.prompt,
            "cuda_peak_bytes": torch.cuda.max_memory_allocated(),
        }


def main():
    setup_logging()
    serve(MolmoBotRunner, load_before_accept=False)


if __name__ == "__main__":
    main()
