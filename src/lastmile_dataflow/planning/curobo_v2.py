"""Pinned cuRoboV2 native grasp pipeline with 11D holobase/torso coupling."""
import hashlib
import importlib.metadata
from pathlib import Path
import time
import numpy as np

from ..io import read_json, write_json
from ..integrations.waypoints import PlanResult
from .curobo import body_pose, tcp_pose, pose7
from .rby1m_mobile_torso import derive_urdf, robot_config, joint_names, ordered_points, retime_points, static_ik_state, chain_state, minimum_mesh_query_dimension
from .collision_world_v2 import collision_world
from .curobo_v2_costs import manager_type, fov_optimizer_configs
from .head_fov import (HeadFOVCost, SparseFOVValidator, camera_definition,
                       camera_robot_config, knot_sample_indices)


def verify_version():
    import curobo
    root = Path(curobo.__file__).parent
    pin = read_json(Path(__file__).resolve().parents[3] / 'configs/curobo-v080-pin.json')
    if importlib.metadata.version('nvidia-curobo') != '0.8.0':
        raise RuntimeError('no-edit V2 requires pinned nvidia-curobo==0.8.0; no legacy fallback')
    for name, digest in pin['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('cuRobo v0.8.0 source mismatch: ' + name)
    return {**pin, 'import_path': str(root)}


class V2Planner:
    mobile_base = True
    torso_active = True

    def __init__(self, sim, config, side, path, *, mobile_base=True, workspace=None):
        import yaml
        import torch
        from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
        from curobo.types import DeviceCfg
        from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg
        if not torch.cuda.is_available() or not mobile_base or workspace is None:
            raise RuntimeError('V2 requires CUDA and an explicit mobile workspace')
        self.identity = verify_version()
        self.sim, self.config, self.side, self.path = sim, config, side, Path(path)
        self.names = joint_names(side)
        self.base = body_pose(sim, sim.model.body('robot_0/base').id)
        self.args = DeviceCfg()
        self.workspace = workspace
        center, radius = workspace
        local = np.linalg.solve(self.base, np.r_[center[:2], self.base[2, 3], 1.])[:2]
        from ..robots.action_limits import local_yaw_bounds
        self.reference_yaw=float(sim.robot.group('base')[2])
        bounds = {'base_x': (local[0]-radius, local[0]+radius),
                  'base_y': (local[1]-radius, local[1]+radius), 'base_theta': local_yaw_bounds(sim.robot,self.reference_yaw)}
        root = Path(config.robot_planner_dir)
        urdf = self.path / 'mobile_torso_mimic.urdf'
        coupling = derive_urdf(root/'urdf/model_holobase.urdf', urdf, bounds, sim.robot.config.torso_limits)
        self.h_step_limit = coupling['h_velocity_limit'] * config.time_dilation * .9 / sim.robot.config.control_hz
        cfg = robot_config(yaml.safe_load((root/'rby1m_holobase.yml').read_text()), sim, side, root, urdf)
        cfg['kinematics']['collision_spheres'] = yaml.safe_load((root/'rby1m_holobase_spheres.yml').read_text())['collision_spheres']
        self.robot_cfg = cfg
        (self.path/'planner.yml').write_text(yaml.safe_dump(cfg, sort_keys=False))
        self.world_range = 2*radius+1.
        world = collision_world(sim, self.path, self.world_range, reference_base=self.base)
        target = collision_world(sim, self.path, self.world_range, reference_base=self.base, target_only=True)
        self.context = {'target_checker': SceneCollision.from_config(SceneCollisionCfg(
            device_cfg=self.args, scene_model=target, cache={'mesh': 2})),
            'contact_links': [f'ee_finger_{side[0]}1', f'ee_finger_{side[0]}2'],
            'contact_allowed': False, 'attached': False,
            'local_center': self.args.to_device(local), 'radius': radius}
        self.fov_log = []
        self.fov_definition = camera_definition(sim, config.width, config.height)
        self.fov_validator = SparseFOVValidator(sim, self.fov_definition, self.base_target_world, self.names,
            min_depth=config.head_fov_min_depth_m)
        if config.head_fov_enabled:
            from curobo._src.types.robot import RobotCfg
            fov_cfg = camera_robot_config(cfg, self.fov_definition)
            self.context.update(fov_robot=RobotCfg.create(fov_cfg, device_cfg=self.args),
                fov_cost=HeadFOVCost(self.fov_definition['tan_half_x'], self.fov_definition['tan_half_y'],
                    margin=config.head_fov_margin, min_depth=config.head_fov_min_depth_m, weight=config.head_fov_weight),
                fov_target=self.args.to_device(np.linalg.solve(self.base, np.r_[sim.data.xpos[sim.target_id], 1.])[:3]),
                fov_tcp_frame=f'ee_{side}_tcp', fov_attached_offset=None)
        owner = self

        class NativeGraspPlanner(MotionPlanner):
            def plan_pose(self, goal_tool_poses, current_state, *args, **kwargs):
                phase = next(owner.phases) if owner.phases is not None else owner.single_phase
                if owner.on_query is not None:
                    owner.on_query(phase)
                owner.context['contact_allowed'] = phase in ('goalset', 'grasp', 'approach_contact')
                if phase == 'lift':
                    owner.attach_target_geometry(current_state)
                started = time.monotonic()
                result = super().plan_pose(goal_tool_poses, current_state, *args, **kwargs)
                if (owner.config.head_fov_enabled and result is not None
                        and bool(result.success.any().item())):
                    report = owner.validate_fov_result(result, phase)
                    if not report['valid']:
                        result.success[:] = False
                        result.status = 'FOV_CONSTRAINT_FAILED'
                if result is not None and result.js_solution is not None:
                    # v0.8.0 _get_best_result augments position with locked joints
                    # but then installs ACTIVE-only B-spline knots under FULL joint
                    # names. Native grasp's get_active_js reindexes those knots and
                    # can assert on CUDA. Knots are optimizer controls, not measured
                    # state; stage chaining requires position/velocity/acceleration.
                    chain_state(result.js_solution)
                owner.query_log.append({'phase': phase, 'wall_time_s': time.monotonic()-started,
                    'max_attempts': kwargs.get('max_attempts', 5),
                    'success': result is not None and bool(result.success.any().item())})
                return result

        self.phases = None; self.single_phase = 'pose'; self.on_query = None; self.query_log = []
        extra_settings = {}
        if config.head_fov_enabled:
            ik_config, trajopt_config, metrics_config = fov_optimizer_configs()
            # v0.8.0 mutates resolved dictionaries while constructing each
            # solver. A local YAML gives IK/TrajOpt fresh metrics configs.
            metrics_path = self.path/'head_fov_metrics.yml'
            metrics_path.write_text(yaml.safe_dump(metrics_config, sort_keys=False))
            extra_settings = {'ik_optimizer_configs': [ik_config], 'trajopt_optimizer_configs': [trajopt_config],
                              'metrics_rollout': str(metrics_path.resolve())}
        settings = MotionPlannerCfg.create(**extra_settings, robot=cfg, scene_model=world, device_cfg=self.args,
            num_ik_seeds=config.num_ik_seeds, num_trajopt_seeds=config.num_trajopt_seeds,
            max_goalset=config.max_candidates, self_collision_check=True,
            optimizer_collision_activation_distance=.01, use_cuda_graph=False,
            random_seed=config.seed, cost_manager_config_instance_type=manager_type(self.context))
        self.motion = NativeGraspPlanner(settings)
        # v0.8.0 MotionPlanner.attachment_manager points to an unimplemented
        # TrajOptSolver attribute. Use the tag's standalone native manager.
        from curobo._src.collision.attachment_manager import AttachmentManager
        self.attachment = AttachmentManager(self.motion.kinematics, self.motion.scene_collision_checker, self.args)
        if set(self.motion.joint_names) != set(self.names) or self.motion.tool_frames != [f'ee_{side}_tcp']:
            raise ValueError('unexpected degrees of freedom or auxiliary pose targets')
        self.pad_target_query_range()
        # Validate the real model/frame convention before any physical action.
        fk = self.motion.compute_kinematics(self.state()).tool_poses.to_dict()[f'ee_{side}_tcp'].get_numpy_matrix().reshape(4, 4)
        actual = np.linalg.solve(self.base, tcp_pose(sim, side))
        translation_error = float(np.linalg.norm(fk[:3, 3] - actual[:3, 3]))
        angle_error = float(np.arccos(np.clip((np.trace(fk[:3, :3].T @ actual[:3, :3])-1)/2, -1, 1)))
        write_json(self.path/'fk_check.json', {'translation_error_m': translation_error, 'rotation_error_rad': angle_error,
            'scope':'initial calibration' if not sim.started else 'measured nonideal mimic tracking telemetry'})
        if not sim.started and (translation_error > .001 or angle_error > .001):
            raise ValueError(f'V2/MuJoCo FK mismatch {translation_error}m/{angle_error}rad')
        if config.head_fov_enabled:
            from curobo._src.robot.kinematics.kinematics import Kinematics
            head_fk = Kinematics(self.context['fov_robot'].kinematics, compute_spheres=False)
            js = self.state().reorder(head_fk.joint_names)
            pose = head_fk.compute_kinematics(js).tool_poses.get_link_pose('head_fov_optical').get_numpy_matrix().reshape(4, 4)
            cid = self.fov_definition['camera_id']
            actual_camera = np.eye(4)
            actual_camera[:3, 3] = sim.data.cam_xpos[cid]
            actual_camera[:3, :3] = sim.data.cam_xmat[cid].reshape(3, 3)
            actual_camera = np.linalg.solve(self.base, actual_camera)
            camera_translation_error = float(np.linalg.norm(pose[:3, 3]-actual_camera[:3, 3]))
            camera_angle_error = float(np.arccos(np.clip((np.trace(pose[:3, :3].T @ actual_camera[:3, :3])-1)/2, -1, 1)))
            write_json(self.path/'head_camera_fk_check.json', {
                **self.fov_definition, 'translation_error_m': camera_translation_error,
                'rotation_error_rad': camera_angle_error, 'head_is_pose_goal': False,
                'margin': config.head_fov_margin, 'weight': config.head_fov_weight})
            if not sim.started and (camera_translation_error > .001 or camera_angle_error > .001):
                raise ValueError('cuRobo/MuJoCo head optical FK mismatch')
        self.motion.warmup(enable_graph=False, num_warmup_iterations=1)
        self.query_log.clear()
        self.fov_log.clear()
        self.solver_log = []
        # Observe the actual native solves; do not issue additional IK searches.
        for kind, solver in (('ik', self.motion.ik_solver), ('trajopt', self.motion.trajopt_solver)):
            original = solver.solve_pose
            def traced(*args, _original=original, _kind=kind, _solver=solver, **kwargs):
                if _kind == 'ik' and kwargs.get('current_state') is not None:
                    # Native grasp passes the previous trajectory's sample dt
                    # into static IK. CSpacePosition then tightens goal bounds
                    # to a SINGLE sample's velocity reach (e.g. 25 ms), making
                    # an 8 cm grasp approach falsely infeasible. Static endpoint
                    # IK has no one-tick duration; trajectory optimization and
                    # real control retain all velocity/acceleration limits.
                    kwargs['current_state'] = static_ik_state(kwargs['current_state'])
                result = _original(*args, **kwargs)
                row = {'solver': _kind, 'success': result.success.detach().cpu().tolist()}
                for field in ('position_error', 'rotation_error', 'feasible'):
                    value = getattr(result, field, None)
                    if value is not None: row[field] = value.detach().cpu().tolist()
                metrics = getattr(result, 'metrics', None)
                if metrics is None and _kind == 'ik' and result.optimized_seeds is not None:
                    # Evaluation only, using already optimized seeds: no new search.
                    metrics = _solver.core.metrics_rollout.compute_metrics_from_action(
                        result.optimized_seeds.reshape(-1, 1, len(self.names)))
                if metrics is not None and metrics.costs_and_constraints is not None:
                    row['constraint_terms'] = {}
                    for category in ('constraints', 'hybrid_costs_constraints'):
                        terms = getattr(metrics.costs_and_constraints, category)
                        for name, value in zip(terms.names, terms.values):
                            row['constraint_terms'][category + '/' + name] = {
                                'min': float(value.min().item()), 'max': float(value.max().item()),
                                'positive_count': int((value > 0).sum().item())}
                            if value.ndim == 3 and value.shape[-1] == self.motion.kinematics.total_spheres:
                                params = self.motion.kinematics.config.kinematics_config
                                ids = result.debug_info.get('seed_idx')
                                if ids is not None:
                                    selected = value[ids.reshape(-1), 0].detach().cpu().numpy()
                                    links = {}
                                    for link in params.link_name_to_idx_map:
                                        sphere_ids = params.get_sphere_index_from_link_name(link).cpu().numpy()
                                        costs = selected[:, sphere_ids].sum(axis=-1)
                                        if (costs > 0).any(): links[link] = costs.tolist()
                                    row['constraint_terms'][category + '/' + name]['selected_link_costs'] = links
                self.solver_log.append(row)
                write_json(self.path/'solver_trace.json', self.solver_log)
                return result
            solver.solve_pose = traced
        write_json(self.path/'planner_identity.json', {**self.identity, **coupling,
            'joint_names': list(self.names), 'tool_frames': self.motion.tool_frames,
            'default_plan_pose_attempts': 5, 'self_collision': True,
            'head_fov_enabled': config.head_fov_enabled, 'head_fov_margin': config.head_fov_margin,
            'head_fov_weight': config.head_fov_weight, 'head_fov_fk': 'position_basis_probes',
            'disabled_collision_links': [], 'target_contact_links': self.context['contact_links']})
        write_json(self.path/'mobile_base.json', {'reference_world': self.base.tolist(),
            'joint_bounds_local': bounds, 'workspace_center_world': list(center[:2]), 'workspace_radius_m': radius})

    def validate_fov_result(self, result, phase):
        trajectory = result.get_interpolated_plan()
        active = trajectory.reorder(list(self.motion.joint_names))
        values = active.position.detach().cpu().numpy().reshape(-1, len(self.names))
        last = result.interpolated_last_tstep
        if last is not None:
            values = values[:int(last.reshape(-1)[0].item())]
        values = ordered_points(values, list(self.motion.joint_names), list(self.names))
        dt = float(trajectory.dt.reshape(-1)[0].item())
        keys = knot_sample_indices(result.js_solution, dt, len(values))
        report = self.fov_validator.validate(values, keys, phase=phase)
        self.fov_log.append(report)
        write_json(self.path/'head_fov_validation.json', self.fov_log)
        return report

    def close(self):
        self.on_query = None
        self.motion.destroy()

    def current_joints(self):
        relative = np.linalg.solve(self.base, body_pose(self.sim, self.sim.model.body('robot_0/base').id))
        return np.r_[relative[:2, 3], float(self.sim.robot.group('base')[2])-self.reference_yaw,
                     self.sim.robot.group(self.side+'_arm'), self.sim.robot.group('torso')[1]]

    def state(self):
        from curobo.types import JointState
        values = ordered_points(self.current_joints(), list(self.names), self.motion.joint_names)
        return JointState.from_position(self.args.to_device(values).view(1, -1), joint_names=self.motion.joint_names)

    def goals(self, matrices):
        from curobo.types import GoalToolPose
        values = np.array([pose7(np.linalg.solve(self.base, matrix)) for matrix in matrices])
        return GoalToolPose(tool_frames=self.motion.tool_frames,
            position=self.args.to_device(values[:, :3]).view(1, 1, 1, len(values), 3),
            quaternion=self.args.to_device(values[:, 3:]).view(1, 1, 1, len(values), 4))

    def base_target_world(self, point):
        x, y, yaw = point[:3]
        local = np.eye(4); local[:2, 3] = [x, y]
        local[:2, :2] = [[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]]
        world = self.base @ local
        return np.r_[world[:2, 3], self.reference_yaw+yaw]

    def points(self, trajectory, last=None):
        trajectory = trajectory.reorder(list(self.motion.joint_names))
        raw = trajectory.position.detach().cpu().numpy().reshape(-1, len(self.names))
        if last is not None:
            raw = raw[:int(last.reshape(-1)[0].item())]
        points = ordered_points(raw, list(self.motion.joint_names), list(self.names))
        if trajectory.dt is None:
            raise ValueError('V2 interpolated trajectory missing timing')
        native_dt = float(trajectory.dt.reshape(-1)[0].item())
        points = retime_points(points, native_dt, self.sim.robot.config.control_hz, self.config.time_dilation)
        if not len(points) or not np.isfinite(points).all():
            raise ValueError('invalid V2 trajectory')
        center, radius = self.workspace
        if any(np.linalg.norm(self.base_target_world(p)[:2]-center[:2]) > radius+1e-6 for p in points):
            raise ValueError('planned trajectory leaves circular workspace')
        return tuple(tuple(float(v) for v in p) for p in points)

    def grasp(self, matrices, on_query, *, lift=True):
        self.phases = iter(('goalset', 'pregrasp', 'grasp', 'lift')); self.on_query = on_query
        try:
            result = self.motion.plan_grasp(self.goals(matrices), self.state(),
                grasp_approach_offset=-.08, grasp_lift_offset=.10,
                grasp_lift_in_tool_frame=False, plan_approach_to_grasp=True,
                plan_grasp_to_lift=lift, disable_collision_links=[])
            success = result.success is not None and bool(result.success.any().item())
            stages = {}
            if success:
                for name, field in (('pregrasp', 'approach'), ('approach', 'grasp'), ('lift', 'lift')):
                    if name == 'lift' and not lift: continue
                    stages[name] = self.points(getattr(result, field+'_interpolated_trajectory'),
                                               getattr(result, field+'_interpolated_last_tstep'))
            index = int(result.goalset_index.reshape(-1)[0].item()) if result.goalset_index is not None else None
            diagnostic = {'status': result.status, 'queries': list(self.query_log), 'grasp_index': index,
                          'native_plan_grasp': True, 'native_full_plan_grasp': bool(lift), 'solver_trace': list(self.solver_log), 'joint_names': list(self.names),
                          'head_fov': list(self.fov_log), 'finite_budget_not_impossibility': True,
                          'constrained_planning': self.config.head_fov_enabled,
                          'failure_reason': ('fov_constraint_failed' if any(not r['valid'] for r in self.fov_log)
                                             else 'constrained_planning_failure' if not success and self.config.head_fov_enabled else None)}
            write_json(self.path/'grasp_result.json', {**diagnostic, 'success': success, 'stages': stages})
            return success, index, stages, diagnostic
        finally:
            self.phases = None; self.on_query = None; self.context['contact_allowed'] = False
            self.detach()
            from curobo._src.cost.tool_pose_criteria import ToolPoseCriteria
            self.motion.update_tool_pose_criteria({self.motion.tool_frames[0]: ToolPoseCriteria()})

    def detach(self):
        params = self.motion.kinematics.config.kinematics_config
        params.reset_link_spheres('attached_object_'+self.side)
        self.context['attached'] = False
        self.context['fov_attached_offset'] = None
        self.fov_validator.attached_offset = None

    def attach_target_geometry(self, state=None):
        import torch
        import trimesh
        from dataclasses import asdict
        from curobo.types import Pose
        from curobo._src.geom.sphere_fit import fit_spheres_to_mesh, SphereFitType
        # Circumscribed bbox cells protrude below an object resting on a table,
        # invalidating the lift's start state. Use native mesh/SDF sphere fitting
        # instead, in the existing 40 allocated slots. No geometry is welded in
        # MuJoCo; this is the carried-object planning approximation only.
        if not hasattr(self, '_carried_fit'):
            mesh = self.context['target_checker'].scene_model.mesh[0]
            geometry = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
            self._carried_fit = fit_spheres_to_mesh(geometry, num_spheres=40,
                fit_type=SphereFitType.VOXEL, compute_metrics=True, device_cfg=self.args)
        fit = self._carried_fit
        if fit.num_spheres == 0:
            raise ValueError('empty carried-object fit')
        spheres = torch.cat((fit.centers, fit.radii[:, None]), dim=-1)
        state = self.state() if state is None else state
        self.attachment.update(spheres, state, link_name='attached_object_'+self.side,
            world_objects_pose_offset=Pose.from_list([0,0,0,1,0,0,0], device_cfg=self.args))
        self.context['attached'] = True
        if self.config.head_fov_enabled:
            tcp = self.motion.compute_kinematics(state).tool_poses.get_link_pose(f'ee_{self.side}_tcp', make_contiguous=True).get_matrix()
            target = self.context['fov_target']
            offset = (tcp[..., :3, :3].transpose(-1, -2) @ (target-tcp[..., :3, 3])[..., None]).squeeze(-1)
            self.context['fov_attached_offset'] = offset.detach().reshape(3)
            self.fov_validator.attached_offset = offset.detach().cpu().numpy().reshape(3)
        info = {'physics_attachment':False, 'sphere_count':fit.num_spheres,
                'fit_type':'native_voxel_mesh_sdf', 'fit_time_s':fit.fit_time_s,
                'fit_debug':fit.debug_info, 'metrics':asdict(fit.metrics) if fit.metrics is not None else None}
        write_json(self.path/'carried_geometry.json', info)
        return info

    def pad_target_query_range(self):
        # v0.8.0 data_mesh.compute_local_sdf[_with_grad] uses half the mesh
        # bounding-box diagonal as query range AND as the no-hit distance.
        # A small target's no-hit distance can be LESS than a large robot
        # sphere radius, producing constant false collision with zero gradient.
        # Inflate the QUERY bound only; vertices/faces and sphere radii stay
        # unchanged. This is conservative, not collision disablement.
        max_radius = float(self.motion.kinematics.config.kinematics_config.link_spheres[..., 3].max().item())
        minimum_dimension = minimum_mesh_query_dimension(max_radius)
        for data in self.context['target_checker'].data.get_valid_data():
            if hasattr(data, 'dims'):
                data.dims[..., :3].clamp_(min=minimum_dimension)
        write_json(self.path/'target_query_range.json', {'largest_robot_sphere_radius_m':max_radius,
            'minimum_query_bound_dimension_m':minimum_dimension, 'geometry_changed':False,
            'reason':'no-hit SDF must exceed every queried sphere radius plus activation margin'})

    def refresh_world(self, path):
        import torch
        torch.cuda.synchronize()
        # No graph capture: no outstanding queries may refer to discarded BVHs.
        for checker in (self.motion.scene_collision_checker, self.context['target_checker']):
            for data in checker.data.get_valid_data():
                if hasattr(data, 'wp_cache'): data.wp_cache.clear()
        if hasattr(self, '_carried_fit'): del self._carried_fit
        self.motion.update_world(collision_world(self.sim, Path(path), self.world_range, reference_base=self.base))
        self.context['target_checker'].load_collision_model(collision_world(
            self.sim, Path(path), self.world_range, reference_base=self.base, target_only=True))
        self.pad_target_query_range()
        if self.config.head_fov_enabled:
            self.context['fov_target'] = self.args.to_device(np.linalg.solve(
                self.base, np.r_[self.sim.data.xpos[self.sim.target_id], 1.])[:3])
            self.fov_validator.target_world = self.sim.data.xpos[self.sim.target_id].copy()

    def plan(self, goal):
        result = self.motion.plan_pose(self.goals([goal]), self.state())
        success = result is not None and bool(result.success.any().item())
        points = self.points(result.get_interpolated_plan()) if success else ()
        fov_failed = result is not None and getattr(result, 'status', None) == 'FOV_CONSTRAINT_FAILED'
        return PlanResult('success' if points else 'fov_constraint_failed' if fov_failed else 'no_solution', self.names, points,
            {'status': 'success' if points else 'FOV_CONSTRAINT_FAILED' if fov_failed else 'V2_POSE_FAIL',
             'failure_reason': ('fov_constraint_failed' if fov_failed else
                                'constrained_planning_failure' if not points and self.config.head_fov_enabled else None),
             'head_fov': list(self.fov_log), 'queries': list(self.query_log),
             'finite_budget_not_impossibility': True, 'constrained_planning': self.config.head_fov_enabled})

    def free_ik_diagnostic(self, goal):
        return {'scope': 'V2 full collision solve failed; no global unreachability claim'}
