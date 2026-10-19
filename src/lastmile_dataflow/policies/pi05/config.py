"""Local π0.5 checkpoint and embodiment transforms."""
from dataclasses import dataclass

from ...config import read_config


@dataclass(frozen=True)
class Pi05Config:
    embodiment: str
    checkpoint: str
    tokenizer_path: str
    python: str = '.venv-pi05/bin/python'
    norm_stats: str | None = None
    normalization: str = 'mean_std'
    replan_steps: int = 5
    task_horizon: int = 400
    sample_steps: int = 10
    load_timeout_s: float = 900.
    rpc_timeout_s: float = 120.

    def __post_init__(self):
        if self.embodiment not in ('panda_omron', 'rby1m'):
            raise ValueError('unsupported π0.5 embodiment')
        if self.normalization not in ('mean_std', 'quantile'):
            raise ValueError('unsupported normalization')
        if not 1 <= self.replan_steps <= 50 or min(self.task_horizon, self.sample_steps) < 1:
            raise ValueError('invalid π0.5 action budget')
        if min(self.load_timeout_s, self.rpc_timeout_s) <= 0:
            raise ValueError('invalid π0.5 service timeout')

    @property
    def state_size(self):
        return 16 if self.embodiment == 'panda_omron' else 22

    @property
    def action_size(self):
        return 12 if self.embodiment == 'panda_omron' else 20


def load_config(path):
    values = read_config(path, data_fields=('checkpoint', 'tokenizer_path', 'norm_stats'), repo_fields=('python',))
    backend = values.pop('backend', 'pi05')
    if backend != 'pi05':
        raise ValueError('expected pi05 policy config')
    return Pi05Config(**values)
