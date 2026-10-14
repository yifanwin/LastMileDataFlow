"""按需导出 MolmoBot 观测/动作，不影响源采集结果。"""
from pathlib import Path
import numpy as np

from ..io import create_run, read_json


class UnsupportedMolmoBotData(ValueError):
    pass


def state22(groups):
    parts = []
    for name in ('base', 'left_arm', 'left_gripper', 'right_arm', 'right_gripper', 'torso'):
        values = np.asarray(groups[name]['position'], float)
        parts.append(values[[1, 2, 3]] if name == 'torso' else values[:1] if name.endswith('gripper') else values)
    return np.concatenate(parts).astype(np.float32)


def export_molmobot(attempt, output, *, state_images_only=False, gripper_map=None,
                   seed=0, chunk_length=16, conditioning_point=None):
    source = Path(attempt).resolve()
    recording = read_json(source / 'recording.json')
    if not recording['complete']:
        raise UnsupportedMolmoBotData('recording incomplete')
    output = Path(output)
    native = recording['controller_profile'] == 'molmobot-native-v1'
    if not state_images_only and not native and gripper_map is None:
        raise UnsupportedMolmoBotData('position grippers require an explicit calibrated motor map')
    if gripper_map and not gripper_map.get('calibration_source'):
        raise UnsupportedMolmoBotData('gripper map needs calibration_source')
    if recording['task']['operation'] == 'open' and conditioning_point is None:
        raise UnsupportedMolmoBotData('open requires first-frame conditioning_point')
    create_run(output, {'source': str(source), 'seed': seed, 'chunk_length': chunk_length,
                       'state_images_only': state_images_only, 'conditioning_point': conditioning_point,
                       'gripper_map': gripper_map})
    with np.load(source / 'trajectory.npz', allow_pickle=False) as trajectory:
        states = trajectory['state22']
        values = {'state': states, 'time_s': trajectory['time_s']}
        if not state_images_only:
            actions = trajectory['actions'].copy()
            if not native:
                for side, index in [('left', 10), ('right', 18)]:
                    mapping = gripper_map[side]
                    actions[:, index] = np.interp(actions[:, index], mapping['position_m'], mapping['motor'])
            values['actions'] = actions
        np.savez_compressed(output / 'data.npz', **values)
    # Images remain in the source rollout; no duplicate model dataset tree.
    return output
