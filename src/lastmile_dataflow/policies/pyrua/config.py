"""Explicit PyRUA agent, RPent primitive and RLDX-1 service configuration."""
from dataclasses import dataclass
import sys

from ...config import read_config

RUNTIMES = ('codex', 'claude', 'openai')


@dataclass(frozen=True)
class PyruaConfig:
    embodiment: str
    pyrua_root: str          # PyRUA-Lean checkout, read-only reference source
    rpent_root: str          # RPent checkout (robots/robocasa primitives)
    site_packages: str       # RPent/MCP Python dependencies, appended after .venv site-packages
    vla_model_path: str      # RLDX-1-FT-RC365 weights
    vla_hf_home: str         # RLDX-1-VLM support files (HF cache)
    vla_site_packages: tuple = ()   # RLDX-only packages and source, ahead of .venv in the RLDX process
    vla_python: str = '.venv/bin/python'
    runtime: str = 'codex'
    model: str = 'gpt-6-astra'
    reasoning_effort: str = 'xhigh'
    arm: str = 'cells'
    codex_bin: str = 'codex'
    codex_home: str | None = None
    max_turns: int | None = 30
    images: str = 'on-motion'
    feedback: str = 'rich'
    primitives: str = 'full'
    task_feedback: str = 'verdict'
    attempt_timeout_s: float = 2400.
    control_step_budget: int = 6000
    rldx_max_chunks: int = 40
    rldx_action_steps_per_chunk: int = 8
    rldx_settle_patience: int = 999
    vla_load_timeout_s: float = 600.

    def __post_init__(self):
        if self.embodiment != 'panda_omron':
            raise ValueError('PyRUA RoboCasa primitives support only the Panda Omron robocasa-v1 profile')
        if self.runtime not in RUNTIMES or self.arm not in ('cells', 'program'):
            raise ValueError('unsupported PyRUA runtime or arm')
        if self.primitives not in ('full', 'no-vla') or self.task_feedback not in ('verdict', 'success_only'):
            raise ValueError('unsupported PyRUA primitive set or task feedback')
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError('invalid PyRUA decision budget')
        if min(self.attempt_timeout_s, self.vla_load_timeout_s) <= 0 or self.control_step_budget < 1:
            raise ValueError('invalid PyRUA budget')
        if min(self.rldx_max_chunks, self.rldx_action_steps_per_chunk, self.rldx_settle_patience) < 1:
            raise ValueError('invalid RLDX settings')

    @property
    def task_horizon(self):
        return self.control_step_budget

    def import_paths(self):
        """Make PyRUA, RPent and their pure-Python dependencies importable.

        Appended, never prepended: the RPent checkout has its own top-level
        `tests`, and the overlay must not shadow packages of the project .venv.
        """
        for path in (f'{self.pyrua_root}/src', self.rpent_root, self.site_packages):
            if path not in sys.path:
                sys.path.append(path)


def load_config(path):
    values = read_config(path, data_fields=('rpent_root', 'site_packages', 'vla_model_path', 'vla_hf_home',
                                            'vla_site_packages'),
                         repo_fields=('pyrua_root', 'vla_python', 'codex_home'))
    if values.pop('backend', 'pyrua') != 'pyrua':
        raise ValueError('expected pyrua policy config')
    # Ordered {name: path} entries, searched in this order by the RLDX process.
    values['vla_site_packages'] = tuple(values.get('vla_site_packages', {}).values())
    return PyruaConfig(**values)
