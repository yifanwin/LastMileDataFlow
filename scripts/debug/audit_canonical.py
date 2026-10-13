"""Read-only protocol audit; never infers task success or rewrites evidence."""
from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from lastmile_dataflow.io import file_digest, read_json
from lastmile_dataflow.recording.protocol import rows, safe_path


SCHEMA = 'raw-first-canonical/1.0.0'


def files_digest(root, *, exclude=()):
    root = Path(root)
    return {str(p.relative_to(root)): file_digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and str(p.relative_to(root)) not in exclude}


def audit_canonical(path):
    root = Path(path)
    issues = []
    try:
        for folder in ('raw', 'canonical'):
            if (root / folder).is_symlink() or any(p.is_symlink() for p in (root / folder).rglob('*')):
                raise ValueError('raw/canonical cannot contain symlinks')
        artifacts_path = root / 'artifacts.json'
        if artifacts_path.is_symlink():
            raise ValueError('artifact manifest cannot be a symlink')
        if artifacts_path.is_file():
            artifacts = read_json(artifacts_path)['files_sha256']
            if artifacts != files_digest(root, exclude=('artifacts.json',)):
                issues.append('whole_attempt_artifact_checksum_mismatch')
        manifest = read_json(root / 'canonical/manifest.json')
        if manifest['schema_version'] not in (SCHEMA, 'research-canonical/2.0.0'):
            raise ValueError('unsupported schema: ' + manifest['schema_version'])
        if manifest.get('collection_profile') != 'mujoco-rby1-live-v1':
            raise ValueError('unsupported collection profile')
        if manifest['schema_version'] == SCHEMA:
            if not manifest.get('sealed'):
                issues.append('package_not_sealed')
            stored = read_json(root / 'canonical/checksums.json')['files_sha256']
            actual = {}
            for folder in ('raw', 'canonical'):
                actual.update({folder + '/' + k: v for k, v in files_digest(root / folder,
                    exclude=('checksums.json',) if folder == 'canonical' else ()).items()})
            if stored != actual:
                issues.append('canonical_checksum_or_file_set_mismatch')
        elif manifest.get('status') != 'complete':
            issues.append('episode_not_finalized')
        if not manifest.get('recording_complete'):
            issues.append('recording_incomplete')
        mapping = read_json(root / 'raw/model-map.json')
        model = Path(manifest.get('compiled_model_source', 'raw/scene/model.mjb'))
        model = model if model.is_absolute() else root / model
        if file_digest(model) != manifest['compiled_model_sha256']:
            issues.append('compiled_model_identity_mismatch')
        frames = rows(root / 'canonical/frames.jsonl')
        states = rows(root / 'raw/state-index.jsonl')
        groups = rows(root / 'canonical/robot-state.jsonl')
        actions = rows(root / 'canonical/executed-actions.jsonl')
        commands = rows(root / 'raw/commands.jsonl')
        intents = rows(root / 'raw/intent-events.jsonl')
        controls = rows(root / 'raw/controls.jsonl')
        contacts = rows(root / 'raw/contacts.jsonl')
        observations = rows(root / 'raw/observations.jsonl')
        poses = rows(root / 'canonical/camera-poses.jsonl')
        calibration = read_json(root / 'canonical/calibration.json')['calibrations']
        if len(frames) != len(actions) + 1 or len(frames) != manifest['frame_count']:
            issues.append('N_intervals_require_N_plus_1_frames')
        if len(states) != len(frames) or len(groups) != len(frames) or len(actions) != manifest['executed_steps']:
            issues.append('state_or_action_count_mismatch')
        for i, row in enumerate(frames):
            if row['frame_id'] != i or states[i]['state_id'] != i or groups[i]['frame_id'] != i:
                issues.append('state_frame_identity_mismatch')
            if i and row['timestamp'] <= frames[i - 1]['timestamp']:
                issues.append('state_timestamps_not_increasing')
            if row['clock_id'] != 'sim' or row['timestamp'] != states[i]['timestamp'] or row['timestamp'] != groups[i]['timestamp']:
                issues.append('boundary_state_clock_mismatch')
            if row['raw_state_ref'] != states[i]['path'] or groups[i]['raw_state_ref'] != states[i]['path']:
                issues.append('raw_state_reference_mismatch')
            with np.load(safe_path(root, states[i]['path']), allow_pickle=False) as z:
                for key, size in (('qpos', 'nq'), ('qvel', 'nv'), ('actuator_ctrl', 'nu')):
                    if z[key].shape != (mapping[size],):
                        issues.append('state_shape_mismatch:' + key)
                    if not np.isfinite(z[key]).all():
                        issues.append('nonfinite_raw_state:' + key)
                for joint in mapping['joints']:
                    if joint['type'] in ('ball', 'free'):
                        adr = joint['qpos_address'] + (3 if joint['type'] == 'free' else 0)
                        if not np.isclose(np.linalg.norm(z['qpos'][adr:adr + 4]), 1, atol=1e-5):
                            issues.append('invalid_joint_quaternion')
                by_name = {j['name']: j for j in mapping['joints']}
                for name, group in groups[i]['groups'].items():
                    joints = [by_name[n] for n in group['joint_names']]
                    q = [float(z['qpos'][j['qpos_address']]) for j in joints]
                    v = [float(z['qvel'][j['qvel_address']]) for j in joints]
                    if not np.allclose(q, np.asarray(group['position'], float), equal_nan=True) or not np.allclose(v, np.asarray(group['velocity'], float), equal_nan=True):
                        issues.append('canonical_group_differs_from_raw:' + name)
        for i, command in enumerate(commands):
            if command['command_id'] != i:
                issues.append('command_id_not_unique_contiguous')
            if command['kind'] == 'command_submitted':
                ref = command['command_raw_id']
                if ref >= i or commands[ref]['kind'] != 'command_raw':
                    issues.append('submitted_command_without_prior_raw')
                if len(command['submitted_action']) != 20 or not np.isfinite(command['submitted_action']).all():
                    issues.append('invalid_submitted_action')
                if command['controller_profile'] != manifest['controller_profile']:
                    issues.append('mixed_controller_profiles')
        for i, intent in enumerate(intents):
            if intent['intent_id'] != i:
                issues.append('intent_id_not_unique_contiguous')
            with np.load(safe_path(root, intent['value_ref']), allow_pickle=False) as z:
                if z['value'].dtype == np.dtype('O'):
                    issues.append('object_intent_array_forbidden')
        for i, control in enumerate(controls):
            if control['control_id'] != i or control['control_tick_id'] != i:
                issues.append('control_id_not_unique_contiguous')
            command = commands[control['command_submitted_id']]
            if command['kind'] != 'command_submitted' or not np.array_equal(control['actuator_ctrl'], command['ctrl']):
                issues.append('applied_ctrl_differs_from_submitted')
        used_controls = []
        tick = 0
        for i, action in enumerate(actions):
            if action['step_id'] != i or action['frame_before_id'] != i or action['frame_after_id'] != i + 1:
                issues.append('action_frame_identity_mismatch')
            if action['clock_id'] != 'sim' or action['t_start'] != frames[i]['timestamp'] or action['t_end'] != frames[i + 1]['timestamp']:
                issues.append('action_boundary_alignment_mismatch')
            dt = action['t_end'] - action['t_start']
            if dt <= 0 or not np.isclose(dt, action['actual_dt'], atol=1e-10) or not np.isclose(dt, action['physics_steps'] * action['physics_dt'], atol=1e-9):
                issues.append('physics_dt_or_interval_duration_mismatch')
            if action['completion'] == 'complete' and action['physics_steps'] != action['requested_physics_steps']:
                issues.append('incomplete_physics_ticks_marked_complete')
            ref = action['command_submitted_id']
            if commands[ref]['command_raw_id'] != action['command_raw_id']:
                issues.append('action_command_chain_mismatch')
            ids = action['applied_control_ids']
            if not ids:
                issues.append('executed_action_without_applied_control')
            remaining = action['physics_steps']
            for control_id in ids:
                control = controls[control_id]
                if control['command_submitted_id'] != ref or control['physics_tick_start'] != tick:
                    issues.append('applied_control_tick_chain_mismatch')
                if not np.isclose(control['timestamp'], action['t_start'] + (tick - frames[i]['physics_tick_id']) * action['physics_dt'], atol=1e-9):
                    issues.append('applied_control_timestamp_mismatch')
                executed = min(remaining, control['requested_physics_ticks'])
                remaining -= executed
                tick += executed
                used_controls.append(control_id)
            if remaining or frames[i + 1]['physics_tick_id'] != tick:
                issues.append('uncovered_or_excess_physics_ticks')
            motion = action['measured_motion']
            a = np.asarray(groups[i]['groups']['base']['planar_xy_yaw'], float)
            b = np.asarray(groups[i + 1]['groups']['base']['planar_xy_yaw'], float)
            delta = b - a
            delta[2] = (delta[2] + np.pi) % (2 * np.pi) - np.pi
            c, s = np.cos(a[2]), np.sin(a[2])
            body = [*(np.array([[c, s], [-s, c]]) @ delta[:2]), delta[2]]
            if not np.allclose(delta, np.asarray(motion['base_delta_world_xy_yaw'], float), equal_nan=True) or not np.allclose(body, np.asarray(motion['base_delta_body_xy_yaw'], float), equal_nan=True):
                issues.append('measured_base_motion_mismatch')
        if len(set(used_controls)) != len(used_controls):
            issues.append('control_reused_in_multiple_actions')
        if tick != manifest['physics_ticks'] or (manifest['sampling_cadence']['contacts'] == 'every_physics_tick' and len(contacts) != tick):
            issues.append('physics_tick_contact_count_mismatch')
        for i, contact in enumerate(contacts):
            if contact['physics_tick_id'] != i + 1:
                issues.append('physics_contact_tick_gap')
            if not np.isclose(contact['timestamp'], frames[0]['timestamp'] + (i + 1) * manifest['physics_dt'], atol=1e-8):
                issues.append('physics_contact_timestamp_mismatch')
        pose_map = {r['sample_id']: r for r in poses}
        sample_map = {r['sample_id']: r for r in observations}
        if len(sample_map) != len(observations) or len(pose_map) != len(poses):
            issues.append('duplicate_sensor_sample_or_pose')
        for observation in observations:
            frame = frames[observation['frame_id']]
            if observation['timestamp'] != frame['timestamp'] or observation['clock_id'] != 'sim':
                issues.append('sensor_state_alignment_mismatch')
            if observation['status'] != 'recorded':
                issues.append('raw_sensor_missing')
                continue
            image = imageio.imread(safe_path(root, observation['path']))
            w, h = observation['resolution']
            if image.shape != (h, w, 3) or image.dtype != np.uint8 or observation['encoding'] != 'lossless_png':
                issues.append('RGB_shape_or_encoding_mismatch')
            cal = calibration[observation['calibration_id']]
            pose = pose_map[observation['sample_id']]
            transform = np.asarray(pose['T_world_camera_optical'], float)
            if transform.shape != (4, 4) or not np.isfinite(transform).all() or not np.allclose(transform[3], [0, 0, 0, 1]):
                issues.append('invalid_camera_transform')
            elif not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(transform[:3, :3]), 1, atol=1e-5):
                issues.append('invalid_camera_rotation')
            native_camera = pose.get('simulator_camera_state')
            if native_camera:
                expected_rotation = np.asarray(native_camera['rotation_world_source']) @ np.asarray(native_camera['source_to_optical_rotation'])
                if not np.allclose(transform[:3, :3], expected_rotation, atol=1e-5) or not np.allclose(transform[:3, 3], native_camera['position_world_m'], atol=1e-5):
                    issues.append('renderer_pose_differs_from_simulator_camera')
            if cal['resolution'] != [w, h] or pose['timestamp'] != observation['timestamp'] or pose['calibration_id'] != observation['calibration_id']:
                issues.append('camera_calibration_identity_mismatch')
            K = np.asarray(cal['intrinsics']['K'], float)
            if K.shape != (3, 3) or not np.isfinite(K).all() or K[0, 0] <= 0 or K[1, 1] <= 0:
                issues.append('invalid_camera_intrinsics')
            depth = observation.get('depth', {})
            if depth.get('status') == 'recorded':
                with np.load(safe_path(root, depth['path']), allow_pickle=False) as z:
                    values, mask = z['depth'], z['valid_mask']
                    expected = np.isfinite(values) & (values >= depth['near_m']) & (values < depth['far_m'] * (1 - 1e-6))
                    if values.shape != (h, w) or mask.dtype != np.bool_ or not np.array_equal(mask, expected):
                        issues.append('depth_shape_or_mask_mismatch')
                if depth['native_unit'] != 'm' or depth['representation'] != 'optical_z':
                    issues.append('unsupported_depth_semantics')
            elif manifest['capabilities']['depth']['status'] != 'disabled':
                issues.append('required_depth_missing')
        if actions:
            for frame in frames:
                refs = frame['observation_refs']
                if set(refs) != set(manifest['camera_profile']['cameras']) or not frame['alignment']['alignment_valid']:
                    issues.append('boundary_missing_required_cameras')
                for name, ref in refs.items():
                    sample = sample_map[ref['sample_id']]
                    if sample['sensor_id'] != name or sample['frame_id'] != frame['frame_id']:
                        issues.append('frame_sensor_reference_mismatch')
        outcome = read_json(root / 'canonical/outcomes.json')
        if outcome['success'] not in (True, False, None) or outcome['recording_complete'] != manifest['recording_complete']:
            issues.append('outcome_semantics_mismatch')
        return {'valid': not issues, 'schema_version': SCHEMA, 'episode_id': manifest['episode_id'],
                'executed_steps': len(actions), 'frame_count': len(frames), 'issues': sorted(set(issues))}
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        return {'valid': False, 'schema_version': SCHEMA,
                'issues': sorted(set(issues + ['invalid_package:' + type(exc).__name__ + ':' + str(exc)]))}

if __name__ == '__main__':
    import argparse, json
    parser = argparse.ArgumentParser(description='Explicit deep episode review, not a collector step')
    parser.add_argument('attempt', type=Path)
    args = parser.parse_args()
    result = audit_canonical(args.attempt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['valid'] else 1)
