"""π0.5 action chunks over the existing project-local process transport."""
import time

from ..molmobot import MolmoBotClient
from .config import load_config


class Pi05Client(MolmoBotClient):
    load_config = staticmethod(load_config)
    service_module = 'lastmile_dataflow.policies.pi05.service'
    policy_name = 'pi05'

    def prepare_environment(self, env):
        # JAX's pip CUDA libraries must not be shadowed by the host CUDA toolkit.
        env.pop('LD_LIBRARY_PATH',None)
        return env

    def __init__(self, path, log_path):
        super().__init__(path, log_path)
        self.attempt = None
        self.chunk = None
        self.index = 0
        self.serial = 0

    def action(self, observation, *, task, attempt, seed, first=False, timeout=None):
        if attempt != self.attempt:
            self.attempt, self.chunk, self.index = attempt, None, 0
        new_chunk = self.chunk is None or self.index >= self.config.replan_steps
        if new_chunk:
            started = time.monotonic()
            self.chunk = super().action(observation, task=task, attempt=attempt, seed=seed, timeout=timeout)
            self.chunk['rpc_s'] = time.monotonic()-started
            self.index = 0
            self.serial += 1
        index = self.index
        self.index += 1
        return {'action': self.chunk['actions'][index].copy(),
                'raw_action': self.chunk['actions'][index].copy(),
                'normalized_action': self.chunk['raw_actions'][index].copy(),
                'new_chunk': new_chunk, 'chunk_index': index, 'chunk_serial': self.serial,
                'inference_s': self.chunk['inference_s'] if new_chunk else 0.,
                'processed_images': self.chunk['processed_images'] if first else None,
                'model_prompt': self.chunk['model_prompt'],
                'model_chunk_state': self.chunk['model_state'], 'conditioning_points': None}
