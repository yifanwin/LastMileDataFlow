"""π0.5 action chunks over the project-local policy process."""
from ..process import PolicyAction, PolicyProcess
from .config import load_config


class Pi05Client:
    def __init__(self, path, log_path):
        self.config = load_config(path)
        # JAX's pip CUDA libraries must not be shadowed by the host CUDA toolkit.
        self.process = PolicyProcess(self.config, path, log_path, name='pi05', drop_env=('LD_LIBRARY_PATH',),
                                     service_module='lastmile_dataflow.policies.pi05.service')
        self.metadata = self.process.metadata
        self.attempt = None
        self.chunk = None
        self.index = 0
        self.serial = 0

    def action(self, observation, *, task, attempt, seed, first=False):
        if attempt != self.attempt:
            self.attempt, self.chunk, self.index = attempt, None, 0
        new_chunk = self.chunk is None or self.index >= self.config.replan_steps
        if new_chunk:
            self.chunk = self.process.predict(observation, operation=task['operation'], attempt=attempt,
                                              seed=seed, first=first)
            self.index = 0
            self.serial += 1
        index = self.index
        self.index += 1
        return PolicyAction(self.chunk['actions'][index].copy(), self.chunk['actions'][index].copy(),
                            new_chunk, index, self.serial, self.chunk['inference_s'] if new_chunk else 0.,
                            self.chunk['model_prompt'], self.chunk['model_state'],
                            self.chunk['processed_images'] if first else None)

    def close(self):
        self.process.close()
