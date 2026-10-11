"""Explicit model source, optional gripper override, and frozen upstream provenance."""
from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import subprocess

from ..config import construct
from ..io import read_json, file_digest, digest


@dataclass(frozen=True)
class MolmoBotConfig:
    source_dir: str
    checkpoint: str
    python: str
    gripper_mode: str = 'model_default'
    task_horizon: int = 400
    service_timeout_s: float = 300.
    load_timeout_s: float = 900.
    min_free_gpu_mb: int = 24576
    max_gpu_utilization: int = 20
    gpu_sample_count: int = 3
    gpu_sample_interval_s: float = 5.

    def __post_init__(self):
        if self.gripper_mode not in ('model_default', 'binary', 'continuous'):
            raise ValueError('unknown gripper mode')
        for name in ('task_horizon', 'min_free_gpu_mb', 'gpu_sample_count'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('invalid ' + name)
        import math
        for name in ('service_timeout_s', 'load_timeout_s', 'gpu_sample_interval_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + name)
        if type(self.max_gpu_utilization) is not int or not 0 <= self.max_gpu_utilization <= 100:
            raise ValueError('invalid GPU utilization')


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


def policy_manifest(config):
    source, checkpoint = Path(config.source_dir), Path(config.checkpoint)
    if not Path(config.python).is_file(): raise FileNotFoundError(config.python)
    files = {str(p.relative_to(source)):file_digest(p)
             for p in sorted((source/'olmo').rglob('*.py'))}
    if not files: raise ValueError('empty model source')
    commit = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    return {'config':asdict(config), 'upstream_commit':commit, 'source_sha256':digest(files),
            'checkpoint_files':{name:file_digest(checkpoint/name) for name in ('config.yaml','model.pt')},
            'upstream_defaults':upstream_defaults(source),
            'camera_profile':'RBY1GoProD455 nominal rig; no extrinsic jitter; upstream fisheye randomness seeded',
            'protocol':'rby1-multitask-molmobot-native-v1'}
