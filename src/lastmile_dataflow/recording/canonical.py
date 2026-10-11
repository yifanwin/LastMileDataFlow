"""Live raw-first sidecar. Never reconstruct evidence from replay or model inputs."""
from dataclasses import asdict
from pathlib import Path
import shutil

import imageio.v2 as imageio
import mujoco
import numpy as np

from ..io import digest, file_digest, write_json
from .protocol import SCHEMA, append, code_identity, diagnostic, files_digest, write_npz


def model_map(sim):
    m, robot = sim.model, sim.robot
    joints = []
    for j in range(m.njnt):
        kind = int(m.jnt_type[j])
        nq, nv = {0: (7, 6), 1: (4, 3), 2: (1, 1), 3: (1, 1)}[kind]
        name = m.joint(j).name
        joints.append({'name': name, 'joint_id': j, 'type': ('free', 'ball', 'slide', 'hinge')[kind],
                       'qpos_address': int(m.jnt_qposadr[j]), 'qpos_size': nq,
                       'qvel_address': int(m.jnt_dofadr[j]), 'qvel_size': nv,
                       'robot': bool(name and name.startswith(robot.config.namespace)),
                       'position_unit': ('xyz_m_quaternion_wxyz', 'quaternion_wxyz', 'm', 'rad')[kind],
                       'velocity_unit': ('linear_m_s_angular_rad_s', 'rad_s', 'm_s', 'rad_s')[kind],
                       'limited': bool(m.jnt_limited[j]), 'range': m.jnt_range[j].tolist()})
    actuators = []
    for i in range(m.nu):
        native_gripper = (robot.config.control_profile == 'molmobot-native-v1' and
                          i in (robot.act_ids['left_finger_act'], robot.act_ids['right_finger_act']))
        robot_actuator = i in robot.act_ids.values()
        actuators.append({'name': m.actuator(i).name, 'ctrl_address': i,
                          'quantity': 'motor_command' if native_gripper else 'position' if robot_actuator else 'actuator_native',
                          'unit': 'native_motor_unit' if native_gripper else 'actuator_native_unit' if not robot_actuator else
                                  ('m' if i in (robot.act_ids['base_x_act'], robot.act_ids['base_y_act'],
                                                robot.act_ids['left_finger_act'], robot.act_ids['right_finger_act']) else 'rad'),
                          'range': m.actuator_ctrlrange[i].tolist(), 'limited': bool(m.actuator_ctrllimited[i]),
                          'transmission_type': int(m.actuator_trntype[i]), 'transmission_ids': m.actuator_trnid[i].tolist(),
                          'gear': m.actuator_gear[i].tolist(), 'gain_type': int(m.actuator_gaintype[i]),
                          'bias_type': int(m.actuator_biastype[i]), 'gain_parameters': m.actuator_gainprm[i].tolist(),
                          'bias_parameters': m.actuator_biasprm[i].tolist()})
    return {'nq': m.nq, 'nv': m.nv, 'nu': m.nu, 'joints': joints, 'actuators': actuators,
            'bodies': [{'body_id': i, 'name': m.body(i).name, 'parent_id': int(m.body_parentid[i])} for i in range(m.nbody)],
            'geoms': [{'geom_id': i, 'name': m.geom(i).name, 'body_id': int(m.geom_bodyid[i])} for i in range(m.ngeom)],
            'sensors': [{'sensor_id': i, 'name': m.sensor(i).name, 'type': int(m.sensor_type[i]),
                         'address': int(m.sensor_adr[i]), 'dimension': int(m.sensor_dim[i]),
                         'object_type': int(m.sensor_objtype[i]), 'object_id': int(m.sensor_objid[i]),
                         'units': 'source_sensor_type_convention'} for i in range(m.nsensor)],
            'groups': {k: [robot.config.namespace + n for n in v] for k, v in robot.groups.items()},
            'qvel_convention': 'MuJoCo_joint_tangent_space; free/ball angular velocity is not quaternion difference'}


def robot_state(sim):
    robot, m, d = sim.robot, sim.model, sim.data
    groups = {}
    for group, names in robot.groups.items():
        units = ['m' if int(m.jnt_type[robot.joints[n]]) == 2 else 'rad' for n in names]
        groups[group] = {'joint_names': [robot.config.namespace + n for n in names],
                         'position': robot.group(group).tolist(),
                         'velocity': [float(d.qvel[m.jnt_dofadr[robot.joints[n]]]) for n in names],
                         'position_units': units, 'velocity_units': [u + '/s' for u in units]}
    base = robot.group('base')
    base_body = m.body(robot.config.namespace + 'base').id
    rotation = d.xmat[base_body].reshape(3, 3)
    yaw_world = float(np.arctan2(rotation[1, 0], rotation[0, 0]))
    velocity = np.zeros(6)
    mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, base_body, velocity, 0)
    groups['base'].update(pose_world_xyz_wxyz=np.r_[d.xpos[base_body], d.xquat[base_body]].tolist(),
                          planar_xy_yaw=[*d.xpos[base_body, :2].tolist(), yaw_world], reference_frame='world_z_up',
                          linear_velocity_world_m_s=velocity[3:].tolist(), angular_velocity_world_rad_s=velocity[:3].tolist(),
                          velocity_reference='MuJoCo_object_centred_world_orientation',
                          body_axes='asset_body_axes', validity='finite' if np.isfinite(base).all() else 'invalid')
    for side in ('left', 'right'):
        key = side + '_gripper'
        position = robot.group(key)
        groups[key].update(gripper_position=position.tolist(), opening_width_m=None,
                           opening_width_reason='no_calibrated_finger_geometry', state='unknown',
                           grasp_state='unknown', motor_command=None,
                           position_target=None)
        # RBY1 signed finger positions: fully open [-.05,+.05], closed [0,0].
        tolerance = .002
        if np.isfinite(position).all():
            groups[key]['state'] = ('closed' if np.max(np.abs(position)) <= tolerance else
                                   'open' if np.max(np.abs(position - np.array([-.05, .05]))) <= tolerance else 'partial')
        groups[key]['state_definition'] = {'version': 'rby1-signed-fingers-v1', 'tolerance_m': tolerance,
                                           'open_positions_m': [-.05, .05], 'closed_positions_m': [0., 0.],
                                           'source': 'RBY1_joint_position_convention_not_motor_command'}
        ctrl = float(d.ctrl[robot.act_ids[side + '_finger_act']])
        if robot.config.control_profile == 'molmobot-native-v1':
            groups[key]['motor_command'] = ctrl
        else:
            groups[key]['position_target'] = ctrl
    groups['torso'].update(coupling_parameter_h=float(robot.group('torso')[1]), h_unit='rad')
    return diagnostic(groups)


def measured_motion(before, after):
    groups = {}
    for name, row in before.items():
        old = np.asarray(row['position'], float)
        new = np.asarray(after[name]['position'], float)
        groups[name] = {'position_before': row['position'], 'position_after': after[name]['position'],
                        'position_delta': (new - old).tolist(), 'units': row['position_units']}
    a, b = np.asarray(before['base']['planar_xy_yaw'], float), np.asarray(after['base']['planar_xy_yaw'], float)
    delta = b - a
    unwrapped = float(after['base']['position'][2] - before['base']['position'][2])
    delta[2] = (delta[2] + np.pi) % (2 * np.pi) - np.pi
    c, s = np.cos(a[2]), np.sin(a[2])
    body_xy = np.array([[c, s], [-s, c]]) @ delta[:2]
    return diagnostic({'groups': groups, 'base_delta_world_xy_yaw': delta.tolist(),
                       'base_delta_body_xy_yaw': [*body_xy.tolist(), float(delta[2])],
                       'base_unwrapped_delta_yaw': unwrapped, 'units': ['m', 'm', 'rad'],
                       'body_reference': 'interval_start_base', 'kind': 'finite_displacement_not_SE2_log'})


class CanonicalRecorder:
    def __init__(self, attempt, sim, config, *, width=None, height=None):
        self.root = Path(attempt.path)
        self.sim, self.config = sim, config
        self.width = width or config['collection'].get('width', 320)
        self.height = height or config['collection'].get('height', 240)
        self.depth_enabled = config['collection'].get('record_depth', True)
        self.frame_id = -1
        self.step_id = 0
        self.physics_tick_id = 0
        self.control_tick_id = 0
        self.command_count = 0
        self.control_count = 0
        self.intent_count = 0
        self.errors = []
        self.calibrations = {}
        self.last_groups = None
        self.closed = False
        self.streams = {}
        for relative in ('raw/state-index.jsonl', 'raw/commands.jsonl', 'raw/intent-events.jsonl', 'raw/controls.jsonl',
                         'raw/observations.jsonl', 'raw/contacts.jsonl', 'canonical/frames.jsonl',
                         'canonical/robot-state.jsonl', 'canonical/executed-actions.jsonl',
                         'canonical/camera-poses.jsonl', 'canonical/scene-states.jsonl', 'canonical/events.jsonl'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            self.streams[relative] = path.open('xb')
        mapping = model_map(sim)
        write_json(self.root / 'raw/model-map.json', mapping)
        raw_scene = self.root / 'raw/scene'
        raw_scene.mkdir()
        mujoco.mj_saveModel(sim.model, str(raw_scene / 'model.mjb'), None)
        self.model_hash = file_digest(raw_scene / 'model.mjb')
        task = config.get('task', {})
        write_json(self.root / 'canonical/task.json', {
            **task, 'task_id': task.get('task_id'),
            'language_instruction': task.get('instruction'), 'language': task.get('language', 'unknown'),
            'task_type': task.get('operation'), 'target_instance_id': task.get('instance_id', task.get('target')),
            'receptacle_instance_id': task.get('receptacle_instance_id'),
            'receptacle_status': 'known' if task.get('receptacle_instance_id') else 'not_applicable' if task.get('operation') in ('pick', 'open') else 'unknown',
            'success_condition': {'profile': config.get('pick_protocol') or config.get('protocol') or
                                            config.get('station_protocol') or config.get('contact_policy'),
                                  'parameters': task.get('success_conditions', {}),
                                  'source': 'frozen_task_and_execution_profile',
                                  'status': 'declared' if config.get('protocol') or config.get('pick_protocol') or config.get('station_protocol') else 'unknown'}})
        source = getattr(sim, 'source', None)
        source_value = getattr(sim, 'frozen_provenance', None)
        if source_value is None:
            source_value = source.provenance() if source is not None and hasattr(source, 'provenance') else None
        if source is not None and getattr(source, 'metadata_path', None) and Path(source.metadata_path).is_file():
            shutil.copyfile(source.metadata_path, raw_scene / 'source-metadata.json')
        self.scene_id = getattr(source, 'scene_id', None)
        initial = sim.observe_state()
        write_json(self.root / 'canonical/scene.json', diagnostic({
            'scene_id': self.scene_id, 'source': source_value, 'scene_version': config.get('scene_version'),
            'model_sha256': self.model_hash, 'instances': sim.catalog,
            'initial_object_poses': initial['instances'], 'robot_initial_pose': robot_state(sim)['base'],
            'support_relations': {'status': 'unknown', 'reason': 'not_measured_by_generic_recorder'},
            'support_obstacles': task.get('support_obstacles', []),
            'obstacle_source': 'frozen_task_geometry',
            'seeds': {'collection': config['collection'].get('seed'), 'attempt': config.get('seed')},
            'split': getattr(source, 'split', None), 'no_scene_edits': config.get('no_scene_edits')}))
        asset = Path(sim.robot.config.model_path)
        native = sim.robot.config.control_profile == 'molmobot-native-v1'
        self.timing = {'clock_id': 'sim', 'control_hz': 50.0 if native else sim.robot.config.control_hz,
                       'policy_hz': sim.robot.config.control_hz if native else None,
                       'physics_dt': float(sim.model.opt.timestep)}
        cameras = {c: {'role': 'robot_observation', 'policy_input': True, 'available': True}
                   for c in sim.robot.camera_names}
        if sim.third_person_camera is not None:
            cameras[sim.third_person_camera.name] = {'role': 'observer', 'policy_input': False, 'available': True}
        self.cameras = cameras
        run_root = attempt.output_root.parent.parent if attempt.output_root.parent.name == 'scenes' else attempt.output_root
        self.manifest = {
            'schema_version': SCHEMA, 'collection_profile': 'mujoco-rby1-live-v1',
            'run_id': run_root.name, 'episode_id': attempt.attempt_id, 'attempt_id': attempt.attempt_id,
            'task_id': task.get('task_id'), 'scene_id': self.scene_id,
            'clock_definitions': {'sim': {'unit': 's', 'source': 'MuJoCo_data.time', 'origin': float(sim.data.time)}},
            'sampling_cadence': {'state': 'executed_interval_boundary', 'RGB': 'executed_interval_boundary',
                                 'ctrl': 'all_actual_control_submissions', 'contacts': 'every_physics_tick'},
            **self.timing, 'robot_asset_id': sim.robot.config.name, 'robot_asset_version': None,
            'asset_sha256': file_digest(asset) if asset.is_file() else None,
            'asset_identity_status': 'source_hash_available' if asset.is_file() else 'source_asset_unavailable; compiled_model_preserved',
            'compiled_model_sha256': self.model_hash, 'controller_profile': sim.robot.config.control_profile,
            'camera_profile': {'resolution': [self.width, self.height], 'cameras': cameras},
            'simulator_version': mujoco.__version__, 'config_sha256': digest(config), **code_identity(),
            'capabilities': {'rgb': {'available': True, 'status': 'pending'},
                             'depth': {'available': True, 'status': 'pending' if self.depth_enabled else 'disabled',
                                       'reason': None if self.depth_enabled else 'explicit_collection_record_depth_false'},
                             'third_person': {'available': True, 'status': 'enabled' if sim.third_person_camera else 'disabled',
                                              'reason': None if sim.third_person_camera else 'observer_not_enabled'},
                             'actuator_ctrl': {'available': True, 'status': 'recorded'}},
            'durability': {'fsync': 'each_JSONL_record_and_array_chunk',
                           'crash_tail': 'in_flight_tick/frame_may_be_incomplete; no_synthetic_recovery'},
            'sealed': False, 'recording_complete': False, 'execution_status': 'not_executed',
        }
        write_json(self.root / 'canonical/manifest.json', self.manifest)
        self.snapshot('initial')

    def emit(self, relative, value):
        append(self.streams[relative], value)

    def event(self, code, **fields):
        self.emit('canonical/events.jsonl', {'code': code, 'timestamp': float(self.sim.data.time),
                                            **self.timing, **fields})

    def snapshot(self, name):
        native_cache = {}
        targets = getattr(self.sim.robot, '_native_stationary_targets', {})
        flags = getattr(self.sim.robot, '_native_stationary', {})
        for group in ('base', 'left_arm', 'right_arm'):
            native_cache['native_stationary_target_' + group] = np.asarray(targets.get(group, []), float)
            native_cache['native_stationary_flag_' + group] = np.array([flags[group]], bool) if group in flags else np.zeros(0, bool)
        write_npz(self.root / f'raw/scene/{name}.npz', state=self.sim.state_vector(),
                  state_spec=np.array(int(self.sim.state_spec)), model_sha256=np.array(self.model_hash),
                  fixed_head=self.sim.robot.fixed_head, clock_remainder=np.array(self.sim._clock_remainder), **native_cache)

    def capture(self, *, sensors=True):
        sim, d = self.sim, self.sim.data
        self.frame_id += 1
        frame = self.frame_id
        relative = f'raw/states/{frame:06d}.npz'
        write_npz(self.root / relative, qpos=d.qpos.copy(), qvel=d.qvel.copy(),
                  actuator_ctrl=d.ctrl.copy(), act=d.act.copy(), qfrc_actuator=d.qfrc_actuator.copy(),
                  sensordata=d.sensordata.copy())
        timing = {**self.timing, 'timestamp': float(d.time), 'frame_id': frame,
                  'physics_tick_id': self.physics_tick_id}
        self.emit('raw/state-index.jsonl', {**timing, 'state_id': frame, 'path': relative})
        groups = robot_state(sim)
        self.emit('canonical/robot-state.jsonl', {**timing, 'groups': groups,
                                                 'raw_state_ref': relative, 'valid': bool(np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all())})
        observed = sim.observe_state()
        self.emit('canonical/scene-states.jsonl', {**timing, 'instances': observed['instances']})
        observations = {}
        if sensors:
            try:
                captured = sim.capture_sensors(self.width, self.height, depth=self.depth_enabled)
                sim._raw_frame_cache = (float(d.time), self.width, self.height,
                                        {k: v['rgb'] for k, v in captured.items()})
            except Exception as exc:
                captured = {}
                self.errors.append({'frame_id': frame, 'kind': 'sensor_capture', 'error': type(exc).__name__ + ':' + str(exc)})
                self.event('sensor_capture_error', frame_id=frame, error=str(exc))
            for camera in self.cameras:
                sample_id = f'{camera}-{frame:06d}'
                row = {**timing, 'sensor_id': camera, 'sample_id': sample_id,
                       'role': self.cameras[camera]['role'], 'policy_input': self.cameras[camera]['policy_input']}
                sample = captured.get(camera)
                if sample is None:
                    row.update(status='missing', reason='sensor_capture_failed_or_camera_missing')
                    self.errors.append({'frame_id': frame, 'camera': camera, 'kind': 'rgb_missing'})
                else:
                    rgb = np.asarray(sample['rgb'])
                    if rgb.dtype != np.uint8 or rgb.shape != (self.height, self.width, 3):
                        raise ValueError('raw RGB must be configured-resolution uint8 RGB')
                    path = f'raw/observations/{camera}/{frame:06d}.png'
                    target = self.root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    imageio.imwrite(target, rgb)
                    cal = dict(sample['calibration'])
                    pose = cal.pop('T_world_camera_optical')
                    source_camera = cal.pop('source_camera', None)
                    simulator_camera = cal.pop('simulator_camera_state', None)
                    cal_id = digest({'camera': camera, **cal})
                    self.calibrations[cal_id] = {'camera_id': camera, 'calibration_id': cal_id, **cal}
                    self.emit('canonical/camera-poses.jsonl', {**timing, 'sample_id': sample_id,
                        'camera_id': camera, 'calibration_id': cal_id, 'T_world_camera_optical': pose,
                        'source_camera': source_camera, 'simulator_camera_state': simulator_camera})
                    row.update(status='recorded', path=path, calibration_id=cal_id,
                               resolution=[self.width, self.height], dtype='uint8', color_space='renderer_native_RGB',
                               color_transfer='not_calibrated',
                               channel_order='RGB', image_origin='top_left', encoding='lossless_png')
                    if self.depth_enabled and sample.get('depth') is not None:
                        depth = np.asarray(sample['depth'])
                        if depth.shape != (self.height, self.width):
                            raise ValueError('depth resolution mismatch')
                        mask = np.isfinite(depth) & (depth >= cal['near_m']) & (depth < cal['far_m'] * (1 - 1e-6))
                        depth_path = f'raw/depth/{camera}/{frame:06d}.npz'
                        write_npz(self.root / depth_path, depth=depth, valid_mask=mask)
                        row['depth'] = {'status': 'recorded', 'path': depth_path, 'dtype': str(depth.dtype),
                                        'native_unit': 'm', 'scale_to_m': 1.0, 'representation': 'optical_z',
                                        'near_m': cal['near_m'], 'far_m': cal['far_m'],
                                        'valid_range_m': [cal['near_m'], cal['far_m'] * (1 - 1e-6)],
                                        'background_far_epsilon_relative': 1e-6,
                                        'invalid_encoding': 'explicit_mask; original_values_preserved',
                                        'conversion': 'installed_MuJoCo_Renderer_linearized_depth',
                                        'rgb_registration': 'same_camera_same_boundary'}
                    else:
                        error = sample.get('depth_error')
                        row['depth'] = {'status': 'error' if self.depth_enabled else 'disabled',
                                        'reason': error or ('depth_output_missing' if self.depth_enabled else 'explicit_config')}
                        if self.depth_enabled:
                            self.errors.append({'frame_id': frame, 'camera': camera, 'kind': 'depth_missing', 'error': error})
                observations[camera] = {'sample_id': sample_id, 'status': row['status']}
                self.emit('raw/observations.jsonl', row)
        valid = bool(np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all())
        if not valid:
            self.errors.append({'frame_id': frame, 'kind': 'nonfinite_state'})
        self.emit('canonical/frames.jsonl', {**timing, 'raw_state_ref': relative,
                   'robot_state_id': frame, 'observation_refs': observations,
                   'alignment': {'method': 'exact', 'offset_s': 0., 'uncertainty_s': 0.,
                                 'tolerance_s': 0., 'alignment_valid': sensors and bool(observations) and
                                 all(r['status'] == 'recorded' for r in observations.values())}, 'valid': valid})
        self.last_groups = groups
        return frame

    def requested(self, action, *, fixed_base=False):
        request_id = self.command_count
        self.command_count += 1
        row = {'command_id': request_id, 'kind': 'command_raw', 'timestamp': float(self.sim.data.time),
               **self.timing, 'frame_id': self.frame_id, 'value': diagnostic(action),
               'controller_profile': self.sim.robot.config.control_profile, 'fixed_base': fixed_base}
        try:
            array = np.asarray(action)
            if array.dtype.hasobject:
                raise ValueError('object dtype is not a no-pickle raw command array')
            path = f'raw/command-arrays/{request_id:06d}.npz'
            write_npz(self.root / path, command_raw=array)
            row['array_ref'] = path
            row['original_dtype'] = str(array.dtype)
        except (TypeError, ValueError):
            row['original_repr'] = repr(action)
        self.emit('raw/commands.jsonl', row)
        return request_id

    def intent(self, value, *, producer, metadata=None):
        """Preserve input before caller preprocessing, including rejected inputs."""
        intent_id = self.intent_count
        self.intent_count += 1
        path = f'raw/intent-arrays/{intent_id:06d}.npz'
        write_npz(self.root / path, value=np.asarray(value))
        self.emit('raw/intent-events.jsonl', {'intent_id': intent_id, 'timestamp': float(self.sim.data.time),
                  **self.timing, 'producer': producer, 'value_ref': path, 'metadata': metadata or {},
                  'not_execution_evidence': True})

    def submitted(self, command, request_id):
        row_id = self.command_count
        self.command_count += 1
        native = self.sim.robot.config.control_profile == 'molmobot-native-v1'
        layout = [
            {'group': 'base', 'slice': [0, 3], 'mode': 'delta', 'quantity': 'position', 'units': ['m', 'm', 'rad'], 'reference_frame': 'world'},
            {'group': 'left_arm', 'slice': [3, 10], 'mode': 'delta', 'quantity': 'position', 'unit': 'rad'},
            {'group': 'left_gripper', 'slice': [10, 11], 'mode': 'absolute', 'quantity': 'motor_command' if native else 'position', 'unit': 'native_motor_unit' if native else 'm'},
            {'group': 'right_arm', 'slice': [11, 18], 'mode': 'delta', 'quantity': 'position', 'unit': 'rad'},
            {'group': 'right_gripper', 'slice': [18, 19], 'mode': 'absolute', 'quantity': 'motor_command' if native else 'position', 'unit': 'native_motor_unit' if native else 'm'},
            {'group': 'torso', 'slice': [19, 20], 'mode': 'absolute', 'quantity': 'coupling_parameter', 'unit': 'rad'},
        ]
        self.emit('raw/commands.jsonl', {'command_id': row_id, 'kind': 'command_submitted',
                  'command_raw_id': request_id, 'timestamp': float(self.sim.data.time), **self.timing,
                  'reference_frame_id': self.frame_id, 'controller_profile': self.sim.robot.config.control_profile,
                  'layout': layout, **command})
        return row_id

    def control(self, command_id, *, ticks):
        row_id = self.control_count
        self.control_count += 1
        self.emit('raw/controls.jsonl', {'control_id': row_id, 'command_submitted_id': command_id,
                  'control_tick_id': self.control_tick_id, 'timestamp': float(self.sim.data.time), **self.timing,
                  'physics_tick_start': self.physics_tick_id, 'requested_physics_ticks': ticks,
                  'actuator_ctrl': self.sim.data.ctrl.copy(), 'semantics_ref': 'raw/model-map.json',
                  'status': 'submitted', 'hold': 'until_next_submission_or_interval_end'})
        self.control_tick_id += 1
        return row_id

    def physics_tick(self):
        self.physics_tick_id += 1
        d, m = self.sim.data, self.sim.model
        contacts = []
        for i in range(d.ncon):
            contact = d.contact[i]
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, i, force)
            contacts.append({'geom_ids': [int(contact.geom1), int(contact.geom2)],
                             'body_ids': [int(m.geom_bodyid[contact.geom1]), int(m.geom_bodyid[contact.geom2])],
                             'distance_m': float(contact.dist), 'position_world_m': contact.pos.copy(),
                             'contact_frame': contact.frame.copy(), 'force_contact_frame': force,
                             'force_units': ['N', 'N', 'N', 'N*m', 'N*m', 'N*m']})
        self.emit('raw/contacts.jsonl', {'timestamp': float(d.time), **self.timing,
                  'physics_tick_id': self.physics_tick_id, 'contacts': contacts})

    def executed(self, *, command, request_id, submitted_id, controls, start_frame, start_groups,
                 t_start, requested_ticks, actual_ticks, stopped, exception=None):
        if not actual_ticks:
            self.event('submitted_without_execution', command_raw_id=request_id, command_submitted_id=submitted_id)
            return
        end_frame = self.capture()
        t_end = float(self.sim.data.time)
        self.emit('canonical/executed-actions.jsonl', {
            'step_id': self.step_id, **self.timing, 't_start': t_start, 't_end': t_end,
            'actual_dt': t_end - t_start, 'requested_dt': 1 / self.sim.robot.config.control_hz,
            'scheduled_dt': requested_ticks * self.timing['physics_dt'],
            'nominal_interval_dt': 1 / self.sim.robot.config.control_hz,
            'physics_steps': actual_ticks, 'requested_physics_steps': requested_ticks,
            'frame_before_id': start_frame, 'frame_after_id': end_frame,
            'state_before_ref': f'raw/states/{start_frame:06d}.npz',
            'state_after_ref': f'raw/states/{end_frame:06d}.npz',
            'observation_before_refs': {'manifest': 'canonical/frames.jsonl', 'frame_id': start_frame},
            'observation_after_refs': {'manifest': 'canonical/frames.jsonl', 'frame_id': end_frame},
            'command_raw_id': request_id, 'command_submitted_id': submitted_id,
            'applied_control_ids': controls, 'completion': 'partial' if stopped or exception or actual_ticks != requested_ticks else 'complete',
            'termination_reason': str(exception) if exception else diagnostic(stopped),
            'measured_motion': measured_motion(start_groups, self.last_groups),
            'applied_status': 'applied', 'supervision_sources': ['command_submitted', 'applied_control', 'measured_motion']})
        self.step_id += 1

    def finish(self, status, reason, results, extra):
        if self.closed:
            raise RuntimeError('canonical recorder already finalized')
        if self.frame_id < 0:
            self.capture(sensors=False)
        self.snapshot('final')
        write_json(self.root / 'canonical/calibration.json', {'cameras': self.cameras, 'calibrations': self.calibrations})
        metrics = extra.get('trajectory_metrics', {})
        verdict = results.get('task_completion', {}).get('status', 'unknown')
        success = metrics.get('task_success')
        if success is None and verdict in ('success', 'failure') and status != 'simulation_failure':
            success = verdict == 'success'
        if status == 'simulation_failure':
            success = None
        outcome = {'execution_status': 'executed' if self.step_id else 'not_executed',
                   'termination_status': status, 'termination_reason': reason, 'failure_stage': extra.get('phase'),
                   'success': success, 'simulation_valid': metrics.get('simulation_valid'),
                   'protocol_valid': False if status == 'protocol_invalid' else None,
                   'recording_complete': not self.errors, 'collision': None, 'grasp_state': 'unknown',
                   'contact_policy': self.config.get('contact_policy'), 'contact_metrics': metrics,
                   'contact_evidence_ref': 'raw/contacts.jsonl', 'results': results,
                   'quality_labels_do_not_override_task_success': True, 'recording_errors': self.errors}
        write_json(self.root / 'canonical/outcomes.json', outcome)
        for stream in self.streams.values():
            stream.close()
        expert_files = [str(p.relative_to(self.root)) for p in sorted(self.root.iterdir())
                        if p.name == 'planning.json' or 'planner' in p.name or p.name.startswith('dry_plans-')]
        write_json(self.root / 'canonical/expert.json', {'producer': 'molmobot' if 'policy' in self.config else 'curobo' if expert_files else 'unknown',
                                                       'references': expert_files, 'is_generic_action': False})
        self.manifest.update(sealed=True, recording_complete=not self.errors,
                             execution_status=outcome['execution_status'], frame_count=self.frame_id + 1,
                             executed_steps=self.step_id, physics_ticks=self.physics_tick_id,
                             recording_errors=self.errors)
        self.manifest['capabilities']['rgb']['status'] = 'error' if self.errors else 'recorded' if self.step_id else 'not_executed'
        self.manifest['capabilities']['depth']['status'] = ('error' if any(e['kind'] == 'depth_missing' for e in self.errors)
            else 'recorded' if self.step_id and self.depth_enabled else 'not_executed' if self.depth_enabled else 'disabled')
        write_json(self.root / 'canonical/manifest.json', self.manifest)
        checksums = {}
        for name in ('raw', 'canonical'):
            checksums.update({f'{name}/{p}': v for p, v in files_digest(self.root / name).items()})
        write_json(self.root / 'canonical/checksums.json', {'schema_version': SCHEMA, 'files_sha256': checksums})
        self.closed = True
