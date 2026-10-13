"""The only VLA Dataset Adapter: immutable MolmoBot-RBY1Multitask exports."""
import html
import fcntl
import os
from pathlib import Path
import shutil
import uuid

import imageio.v2 as imageio
import numpy as np

from ..io import digest, read_json, write_json
from ..recording.protocol import append, code_identity, safe_path, write_npz
from ..validation.canonical import export_inputs
from .molmobot_images import fisheye, transform_point

ADAPTER_VERSION = 'molmobot-rby1-multitask/1.0.0'
CAMERAS = ('wrist_camera_r', 'head_camera', 'wrist_camera_l')
STATE_LAYOUT = {'base': [0, 3], 'left_arm': [3, 10], 'left_gripper': [10, 11],
                'right_arm': [11, 18], 'right_gripper': [18, 19], 'torso': [19, 22]}


class UnsupportedMolmoBotData(ValueError):
    pass


def state22(groups):
    expected = {'base': 3, 'left_arm': 7, 'left_gripper': 2, 'right_arm': 7, 'right_gripper': 2, 'torso': 6}
    parts = []
    for name, length in expected.items():
        values = np.asarray(groups[name]['position'], float)
        if values.shape != (length,) or not np.isfinite(values).all():
            raise UnsupportedMolmoBotData('missing or invalid RBY1 state group:' + name)
        parts.append(values[[1, 2, 3]] if name == 'torso' else values[:1] if name.endswith('gripper') else values)
    result = np.concatenate(parts).astype(np.float32)
    if result.shape != (22,):
        raise UnsupportedMolmoBotData('state layout mismatch')
    return result


def check_gripper_map(mapping):
    if mapping is None:
        return
    if mapping.get('source_controller_profile') != 'position-v1' or mapping.get('target_controller_profile') != 'molmobot-native-v1' or not mapping.get('calibration_source'):
        raise UnsupportedMolmoBotData('gripper map needs profiles and explicit calibration_source')
    for side in ('left', 'right'):
        row = mapping[side]
        positions = np.asarray(row['position_targets_m'], float)
        motors = np.asarray(row['motor_commands'], float)
        if positions.ndim != 1 or len(positions) < 2 or positions.shape != motors.shape or not np.isfinite(positions).all() or not np.isfinite(motors).all() or np.any(np.diff(positions) <= 0) or np.any(np.abs(motors) > 100):
            raise UnsupportedMolmoBotData('invalid calibrated gripper map:' + side)


def native_action(command, start_groups, *, controller_profile, gripper_map=None):
    targets = command['joint_targets']
    result = np.zeros(20, dtype=np.float32)
    for group, offset in (('base', 0), ('left_arm', 3), ('right_arm', 11)):
        result[offset:offset + len(targets[group])] = np.asarray(targets[group]) - np.asarray(start_groups[group]['position'])
    # Absolute coupling h remains radians, never world torso height.
    result[19] = targets['torso'][1]
    for side, offset in (('left', 10), ('right', 18)):
        group = side + '_gripper'
        if controller_profile == 'molmobot-native-v1':
            result[offset] = command['motor_targets'][group][0]
        elif controller_profile == 'position-v1' and gripper_map:
            row = gripper_map[side]
            target = targets[group][0]
            lo, hi = row['position_targets_m'][0], row['position_targets_m'][-1]
            if not lo <= target <= hi:
                raise UnsupportedMolmoBotData('position target outside calibrated motor mapping')
            result[offset] = np.interp(target, row['position_targets_m'], row['motor_commands'])
        else:
            raise UnsupportedMolmoBotData('position gripper is not motor: supply calibrated --gripper-map or --state-images-only')
    if not np.isfinite(result).all() or np.any(np.abs(result[[10, 18]]) > 100):
        raise UnsupportedMolmoBotData('invalid native action')
    return result


def action_windows(actions, *, interval=.1):
    """Select actual boundaries, no interpolated RGB or invented partial actions."""
    result, current = [], []
    for action in actions:
        current.append(action)
        duration = current[-1]['t_end'] - current[0]['t_start']
        if duration >= interval - 1e-9 or action['completion'] == 'partial':
            valid = abs(duration - interval) <= 1e-8 and all(a['completion'] == 'complete' for a in current)
            result.append((current, valid))
            current = []
    if current:
        result.append((current, False))
    return result


def export_molmobot(attempt, output, *, state_images_only=False, gripper_map=None,
                    seed=0, chunk_length=16, conditioning_point=None):
    source, output = Path(attempt).resolve(), Path(output).absolute()
    if output.resolve().is_relative_to(source):
        raise ValueError('derived output must be outside the source episode')
    if type(seed) is not int or not 0 <= seed < 2 ** 32 or type(chunk_length) is not int or chunk_length < 1:
        raise ValueError('invalid adapter seed or chunk length')
    try:
        record, manifest, inputs = export_inputs(source)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise UnsupportedMolmoBotData('invalid source episode: ' + str(exc)) from exc
    task = read_json(source / 'canonical/task.json')
    if not task.get('language_instruction'):
        raise UnsupportedMolmoBotData('language instruction was not collected')
    if task['task_type'] not in ('pick', 'open'):
        raise UnsupportedMolmoBotData('only pick/open MolmoBot task profiles are supported')
    if task['task_type'] == 'open' and conditioning_point is None:
        raise UnsupportedMolmoBotData('open needs an explicit first-frame normalized --conditioning-point; do not invent a prompt')
    check_gripper_map(gripper_map)
    profile = manifest['controller_profile']
    if gripper_map is not None and (state_images_only or profile != 'position-v1'):
        raise UnsupportedMolmoBotData('gripper mapping is only applicable to position-v1 action exports')
    if not state_images_only and profile != 'molmobot-native-v1' and gripper_map is None:
        raise UnsupportedMolmoBotData('position gripper is not motor: supply calibrated --gripper-map or --state-images-only')
    frames, states, actions, commands = (inputs[k] for k in ('frames', 'states', 'actions', 'commands'))
    robot_limits = read_json(source / 'config.json')['robot']
    obs = {r['sample_id']: r for r in inputs['observations']}
    if len(obs) != len(inputs['observations']):
        raise UnsupportedMolmoBotData('duplicate observation identity')
    if not actions:
        raise UnsupportedMolmoBotData('not_executed episodes have no training transitions')
    windows = action_windows(actions)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / ('.' + output.name + '.lock')
    temporary = output.parent / ('.molmobot-' + uuid.uuid4().hex)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if output.exists() or output.is_symlink():
                raise FileExistsError(output)
            temporary.mkdir()
            sample_rows = []
            state_vectors = []
            action_vectors = []
            valid_actions = []
            first_head_path = None
            prompt = task['language_instruction']
            for index, (window, valid) in enumerate(windows):
                first, last = window[0], window[-1]
                frame_id = first['frame_before_id']
                state = state22(states[frame_id]['groups'])
                state_vectors.append(state)
                refs, transforms = {}, {}
                for camera in CAMERAS:
                    ref = frames[frame_id]['observation_refs'].get(camera)
                    if not ref or ref['status'] != 'recorded':
                        raise UnsupportedMolmoBotData('missing raw camera:' + camera)
                    observation = obs[ref['sample_id']]
                    if observation['frame_id'] != frame_id or observation['sensor_id'] != camera or observation['timestamp'] != frames[frame_id]['timestamp']:
                        raise UnsupportedMolmoBotData('camera/state alignment mismatch')
                    pixels = imageio.imread(safe_path(source, observation['path']))
                    w, h = observation['resolution']
                    if pixels.dtype != np.uint8 or pixels.shape != (h, w, 3):
                        raise UnsupportedMolmoBotData('invalid source RGB')
                    target = temporary / f'images/{index:06d}/{camera}.png'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if camera == 'head_camera':
                        sample_seed = (seed + frame_id) % 2 ** 32
                        pixels, metadata, mapping = fisheye(pixels, seed=sample_seed)
                        map_path = f'images/{index:06d}/head_warp.npz'
                        write_npz(temporary / map_path, **mapping)
                        transforms[camera] = {**metadata, 'map_ref': map_path, 'input_sample_id': ref['sample_id']}
                        if first_head_path is None:
                            first_head_path = str(target.relative_to(temporary))
                            if task['task_type'] == 'open':
                                point = transform_point(conditioning_point, metadata)
                                x, y = (point * 1000).astype(int)
                                label = html.escape(str(task.get('target_instance_id') or 'target'), quote=True)
                                prompt += f' <points coords="1 1 {x:03d} {y:03d}">{label}</points>'
                    else:
                        transforms[camera] = {'kind': 'derived', 'transform': 'identity_lossless_copy', 'input_sample_id': ref['sample_id']}
                    imageio.imwrite(target, pixels)
                    refs[camera] = str(target.relative_to(temporary))
                image_list = [refs[camera] for camera in CAMERAS]
                if task['task_type'] == 'open':
                    image_list.append(first_head_path)
                if not state_images_only:
                    command = commands[last['command_submitted_id']]
                    vector = native_action(command, states[frame_id]['groups'], controller_profile=profile, gripper_map=gripper_map)
                    control_vector = vector.astype(np.float64)
                    safe = (np.linalg.norm(control_vector[:2]) <= robot_limits['max_base_delta'] and
                            abs(control_vector[2]) <= robot_limits['max_yaw_delta'] and
                            np.max(np.abs(np.r_[control_vector[3:10], control_vector[11:18]])) <= robot_limits['max_arm_delta'] and
                            robot_limits['torso_limits'][0] <= control_vector[19] <= robot_limits['torso_limits'][1])
                    action_vectors.append(vector)
                    valid = valid and bool(safe)
                    valid_actions.append(valid)
                sample_rows.append({'sample_id': index, 'timestamp': first['t_start'], 't_end': last['t_end'],
                    'actual_dt': last['t_end'] - first['t_start'], 'source_frame_id': frame_id,
                    'source_step_ids': [a['step_id'] for a in window], 'state_row': index,
                    'action_row': None if state_images_only else index,
                    'action_valid': valid and not state_images_only, 'images': image_list,
                    'native_safe_bounds_valid': None if state_images_only else bool(safe),
                    'image_transforms': transforms, 'prompt': prompt,
                    'supervision_source': None if state_images_only else 'command_submitted_last_absolute_targets_minus_initial_measured_state',
                    'not_equivalent_controller_replay': profile != 'molmobot-native-v1' or len(window) != 1})
            arrays = {'molmobot_22d_state': np.stack(state_vectors),
                      'timestamps': np.array([s['timestamp'] for s in sample_rows]),
                      'actual_dt': np.array([s['actual_dt'] for s in sample_rows])}
            if not state_images_only:
                actions_array = np.stack(action_vectors)
                mask = np.asarray(valid_actions, bool)
                chunks = np.zeros((len(mask), chunk_length, 20), np.float32)
                chunk_mask = np.zeros((len(mask), chunk_length), bool)
                for i in range(len(mask)):
                    count = min(chunk_length, len(mask) - i)
                    chunks[i, :count] = actions_array[i:i + count]
                    chunk_mask[i, :count] = mask[i:i + count]
                arrays.update(molmobot_native_action=actions_array, action_valid_mask=mask,
                              action_chunks=chunks, action_chunk_valid_mask=chunk_mask)
            write_npz(temporary / 'data.npz', **arrays)
            with (temporary / 'samples.jsonl').open('xb') as stream:
                for row in sample_rows:
                    append(stream, row)
            configuration = {'state_images_only': state_images_only, 'seed': seed, 'chunk_length': chunk_length,
                             'gripper_map': gripper_map, 'conditioning_point': conditioning_point}
            write_json(temporary / 'export_config.json', {
                'adapter': ADAPTER_VERSION, 'kind': 'derived', 'status': 'complete',
                'source_episode_id': manifest['episode_id'], 'source_schema_version': manifest['schema_version'],
                'source_path': str(source), 'source_config_sha256': record['config_sha256'],
                'split_group': {'kind': 'source_scene', 'scene_id': record['scene_id']},
                'configuration': configuration, 'configuration_sha256': digest(configuration), **code_identity(),
                'state_layout': STATE_LAYOUT, 'torso_state_indices': [1, 2, 3],
                'camera_order': CAMERAS, 'policy_hz': 10, 'source_controller_profile': profile,
                'target_controller_profile': 'molmobot-native-v1' if not state_images_only else None,
                'action_units': 'world_xy_m_yaw_rad; arms_rad_delta; grippers_native_motor; torso_h_rad',
                'sample_count': len(sample_rows), 'instruction': task['language_instruction'],
                'conversion_report': {'used': ['raw_RGB', 'canonical_joint_state', 'submitted_targets' if not state_images_only else 'no_actions'],
                                      'ignored_in_model_input': ['depth', 'qvel', 'observer', 'expert', 'scene_ground_truth'],
                                      'raw_preserved': True, 'normalization': 'none; checkpoint_preprocessor_responsibility',
                                      'gripper_conversion': 'explicit_calibrated_mapping_not_controller_equivalence' if gripper_map else 'native_direct' if not state_images_only else 'unsupported_not_requested',
                                      'sampling': 'actual_boundary_100ms_windows; incomplete_windows_masked',
                                      'fisheye': 'NumPy_geometry_matches_upstream; uint8_rounding_may_differ_by_one',
                                      'camera_rig_compatibility': 'not_assumed; source_FOV_and_extrinsics_preserved; augmentation_is_not_lens_conversion',
                                      'open_prompt': 'explicit_first_frame_point; same_realized_warp_coefficients' if task['task_type'] == 'open' else 'language_only'}})
            if output.exists() or output.is_symlink():
                raise FileExistsError(output)
            os.rename(temporary, output)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return output
