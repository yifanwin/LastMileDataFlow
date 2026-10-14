"""Explicit model source, optional gripper override, and upstream model configuration."""
from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import subprocess

from ..config import construct
from ..io import read_json


@dataclass(frozen=True)
class MolmoBotConfig:
    source_dir: str
    checkpoint: str
    python: str
    gripper_mode: str = 'model_default'
    task_horizon: int = 400
    service_timeout_s: float = 300.
    rpc_timeout_s: float = 30.
    load_timeout_s: float = 900.

    def __post_init__(self):
        if self.gripper_mode not in ('model_default', 'binary', 'continuous'):
            raise ValueError('unknown gripper mode')
        for name in ('task_horizon',):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('invalid ' + name)
        import math
        for name in ('service_timeout_s', 'rpc_timeout_s', 'load_timeout_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + name)


def load_policy_config(path):
    path = Path(path).resolve(); value = read_json(path)
    for key in ('source_dir', 'checkpoint', 'python'):
        value[key] = str((path.parent / value[key]).absolute())
    return construct(MolmoBotConfig, value)


def upstream_defaults(source):
    """Read the chosen SIM configs without importing an unrelated simulation engine."""
    tree = ast.parse((Path(source)/'olmo/eval/configure_molmo_spaces.py').read_text())
    classes = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            values = {}
            for base in node.bases:
                if isinstance(base, ast.Name): values.update(classes.get(base.id, {}))
            for field in node.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    try: values[field.target.id] = ast.literal_eval(field.value)
                    except (ValueError, TypeError): pass
            classes[node.name] = values
    pick = classes['MolmoBotRBY1PickPnPPolicyConfig']
    opening = classes['MolmoBotRBY1DoorPlusOpenPolicyConfig']
    keys = ('camera_names', 'action_horizon', 'execute_horizon', 'action_move_group_names',
            'action_spec', 'state_spec', 'state_indices', 'clamp_gripper', 'gripper_threshold',
            'cameras_to_warp', 'point_prompt_camera', 'use_point_prompts',
            'use_conditioning_image', 'max_conditioning_points')
    defaults = {op:{k:values[k] for k in keys} for op,values in [('pick',pick), ('open',opening)]}
    timing = classes['SynthVLARBY1EvalConfig']
    if [timing[k] for k in ('policy_dt_ms','ctrl_dt_ms','sim_dt_ms')] != [100.,20.,4.]:
        raise ValueError('unsupported upstream timing: update bridge explicitly')
    for values in defaults.values():
        if sum(values['action_spec'].values()) != 20 or sum(values['state_spec'].values()) != 22:
            raise ValueError('upstream action/state protocol changed')
    return defaults
