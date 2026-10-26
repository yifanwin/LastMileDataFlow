"""Pinned cuRobo V2 grasp pipeline driven by the robot model description."""
import importlib.metadata
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
import time
import numpy as np

from ..utils.io import write_json
from ..utils.logging_config import trial_progress

from .waypoints import PlanResult, GRASP_APPROACH_DISTANCE_M
from .evidence import lane_success, phase_evidence, solver_evidence, ik_probe_evidence
from ..scenes.geometry import body_pose, tcp_pose, pose7, descendants
from .rby1m_mobile_torso import ordered_points, retime_points, static_ik_state, chain_state, minimum_mesh_query_dimension
from .collision_world_v2 import collision_world
from .curobo_v2_costs import manager_type, fov_optimizer_configs
from .head_fov import HeadFOVCost, SparseFOVValidator, camera_definition, camera_robot_config, knot_sample_indices


class PlannerUnavailable(RuntimeError):
    shared_resource = True


def verify_version():
    version = importlib.metadata.version('nvidia-curobo')
    if version != '0.8.0':
        raise PlannerUnavailable('this planner requires nvidia-curobo==0.8.0')
    return {'version': version}


def reset_motion_seed(motion, seed):
    """v0.8.0 random_seed configures the two SolverCore seed managers.

    Other rollout/optimizer/PRM samplers have their own fixed configuration
    seeds. Reset those as a fresh planner does; recreate the two trial-seeded
    managers rather than resetting the previous trial's random stream.
    """
    for solver in (motion.ik_solver, motion.trajopt_solver):
        core = solver.core
        core.config.random_seed = seed
        core.seed_manager = type(core.seed_manager)(
            core.config.device_cfg, core.action_dim,
            core.auxiliary_rollout.action_bound_lows, core.auxiliary_rollout.action_bound_highs,
            random_seed=seed, action_horizon=core.auxiliary_rollout.action_horizon)
    motion.reset_seed()


def bind_batch_seeds(motion, seeds):
    """Bind core and default LM-IK random samples to individual trial seeds."""
    import torch

    for solver in (motion.ik_solver, motion.trajopt_solver):
        core = solver.core
        streams = [type(core.seed_manager)(
            core.config.device_cfg, core.action_dim,
            core.auxiliary_rollout.action_bound_lows, core.auxiliary_rollout.action_bound_highs,
            random_seed=seed, action_horizon=core.auxiliary_rollout.action_horizon) for seed in seeds]

        def generate(batch_size, num_seeds, streams=streams):
            return torch.cat([stream.generate_random_actions(1, num_seeds) for stream in streams], dim=0)

        core.seed_manager.generate_random_actions = generate

    seed_solver = motion.ik_solver.seed_ik_solver
    if seed_solver is not None:
        from curobo._src.util.sampling import SampleBuffer
        streams = [SampleBuffer.create_halton_sample_buffer(
            ndims=seed_solver.dof, device_cfg=seed_solver.device_cfg,
            up_bounds=seed_solver.action_max, low_bounds=seed_solver.action_min,
            seed=seed) for seed in seeds]

        def generate_lm(batch_size, seed_config=None):
            provided = 0 if seed_config is None else seed_config.shape[1]
            remaining = seed_solver.config.num_seeds - provided
            if not remaining:
                return seed_config
            samples = torch.stack([stream.get_samples(remaining, bounded=True) for stream in streams])
            if seed_config is not None:
                return torch.cat((seed_config, samples), dim=1)
            samples[:, -1] = seed_solver._robot_model.default_joint_state.position
            return samples

        seed_solver._generate_seed_configs = generate_lm


class StationPlannerCache:
    """Own at most one pre-close planner per arm, for one frozen station."""
    def __init__(self):
        self.planners = {}

    def acquire(self, sim, config, side, path, *, workspace):
        # Navigation/settling/retries and closed-finger stages use measured state.
        if sim.started:
            return V2Planner(sim, config, side, path, workspace=workspace)
        planner = self.planners.get(side)
        if planner is not None and not planner.matches_initial(sim):
            planner.close()
            del self.planners[side]
            planner = None
        if planner is None:
            planner = V2Planner(sim, config, side, path, workspace=workspace)
            self.planners[side] = planner
        else:
            planner.bind_trial(sim, config, path)
        return planner

    def release(self, planner):
        planner.detach()
        planner.on_query = None
        if not any(planner is cached for cached in self.planners.values()):
            planner.close()

    def close(self):
        for planner in self.planners.values():
            planner.close()
        self.planners.clear()


class PlannerResources:
    """A GPU process retains at most two compatible solver families."""
    def __init__(self):
        self.planners = OrderedDict()

    @contextmanager
    def acquire(self, sim, config, side, path, *, workspace, batch_size, ik_only=False):
        key = (sim.robot.config.model_path, sim.robot.config.control_profile, side,
               config.operation_base_mode, batch_size, config.num_ik_seeds,
               config.num_trajopt_seeds, config.max_candidates, ik_only)
        planner = self.planners.get(key) if not config.head_fov_enabled else None
        if planner is None:
            while len(self.planners) >= 2:
                _, old = self.planners.popitem(last=False)
                old.close()
            planner = V2Planner(sim, config, side, path, workspace=workspace, batch_size=batch_size, ik_only=ik_only)
            if not config.head_fov_enabled:
                self.planners[key] = planner
        else:
            self.planners.move_to_end(key)
            planner.rebind_resources(sim, config, path, workspace)
        try:
            yield planner
        finally:
            planner.detach()
            planner.on_query = None
            if any(planner is item for item in self.planners.values()):
                planner.sim = planner.model.sim = None
            else:
                planner.close()

    def close(self):
        for planner in self.planners.values():
            planner.close()
        self.planners.clear()


class V2Planner:
    def __init__(self, sim, config, side, path, *, workspace, batch_size=1, ik_only=False):
        setup_started = time.monotonic()
        self.progress_ready = False
        import yaml
        try:
            import torch
            if not torch.cuda.is_available():
                raise PlannerUnavailable('V2 requires CUDA')
            from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
            from curobo._src.motion.motion_planner_batch import BatchMotionPlanner
            from curobo.types import DeviceCfg
            from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg
        except ImportError as exc:
            raise PlannerUnavailable(f'cuRobo dependency unavailable: {exc}') from exc
        self.identity = verify_version()
        self.batch_size = batch_size
        self.sim, self.config, self.side, self.path = sim, config, side, Path(path)
        self.initial_model = sim.model
        self.initial_state = sim.state_vector().copy() if not sim.started else None
        self.resource_path = self.path
        self.model = sim.robot.planning_model(sim, config, side, path, workspace)
        self.names = self.model.names
        self.base = self.model.base
        self.args = DeviceCfg()
        self.workspace = workspace
        center, radius = workspace
        local = np.linalg.solve(self.base, np.r_[center[:2], self.base[2, 3], 1.])[:2]
        self.reference_yaw = self.model.reference_yaw
        self.h_step_limit = self.model.h_step_limit
        bounds, coupling, cfg = self.model.bounds, self.model.coupling, self.model.cfg
        self.robot_cfg = cfg
        (self.path/'planner.yml').write_text(yaml.safe_dump(cfg, sort_keys=False))
        self.world_range = 2*radius+1.
        self.target_bodies = descendants(sim.model, sim.target_id)
        self.handle_bodies = descendants(sim.model, sim.handle_id) if getattr(sim, 'handle_id', None) else set()
        world = collision_world(sim, self.path, self.world_range, reference_base=self.base)
        target = collision_world(sim, self.path, self.world_range, reference_base=self.base, target_only=True,
                                 bodies=self.target_bodies - self.handle_bodies)
        handle = (collision_world(sim, self.path, self.world_range, reference_base=self.base, target_only=True,
                                  bodies=self.handle_bodies, filename='handle_world.json')
                  if self.handle_bodies else None)
        self.context = {'target_checker': SceneCollision.from_config(SceneCollisionCfg(
            device_cfg=self.args, scene_model=target, cache={'mesh': 2})),
            'handle_checker': (SceneCollision.from_config(SceneCollisionCfg(
                device_cfg=self.args, scene_model=handle, cache={'mesh': 2})) if handle is not None else None),
            'contact_links': self.model.contact_links,
            'handle_contact_links': getattr(self.model, 'handle_contact_links', self.model.contact_links),
            'contact_allowed': False, 'attached': False,
            'local_center': self.args.to_device(local), 'radius': radius, 'movable_base': self.model.movable_base}
        self.fov_log = []
        self.fov_validator = None
        if config.head_fov_enabled:
            self.fov_definition = camera_definition(sim, config.width, config.height)
            self.fov_validator = SparseFOVValidator(sim, self.fov_definition, self.base_target_world, self.names,
                min_depth=config.head_fov_min_depth_m)
            from curobo._src.types.robot import RobotCfg
            fov_cfg = camera_robot_config(cfg, self.fov_definition)
            self.context.update(fov_robot=RobotCfg.create(fov_cfg, device_cfg=self.args),
                fov_cost=HeadFOVCost(self.fov_definition['tan_half_x'], self.fov_definition['tan_half_y'],
                    margin=config.head_fov_margin, min_depth=config.head_fov_min_depth_m, weight=config.head_fov_weight),
                fov_target=self.args.to_device(np.linalg.solve(self.base, np.r_[sim.data.xpos[sim.target_id], 1.])[:3]),
                fov_tcp_frame=self.model.tcp_link, fov_attached_offset=None)
        owner = self

        native_type = BatchMotionPlanner if batch_size > 1 else MotionPlanner

        class NativeGraspPlanner(native_type):
            def plan_pose(self, goal_tool_poses, current_state, *args, **kwargs):
                phase = next(owner.phases) if owner.phases is not None else owner.single_phase
                owner.progress_phase = phase
                owner.query_index = len(owner.query_log) + 1
                if owner.on_query is not None:
                    owner.on_query(phase)
                kwargs.setdefault('max_attempts', owner.config.max_attempts)
                owner.context['contact_allowed'] = phase in ('goalset', 'grasp', 'approach_contact')
                if phase == 'lift':
                    owner.attach_target_geometry(current_state)
                started = time.monotonic()
                if owner.progress_ready:
                    trial_progress('Planning phase=%s', phase)
                result = super().plan_pose(goal_tool_poses, current_state, *args, **kwargs)
                native_success = lane_success(None if result is None else result.success, owner.evidence_batch_size)
                fov_rejected = [False] * owner.evidence_batch_size
                if (owner.config.head_fov_enabled and result is not None
                        and bool(result.success.any().item())):
                    report = owner.validate_fov_result(result, phase)
                    if not report['valid']:
                        fov_rejected = native_success
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
                    'query_index': owner.query_index, 'goal_count': goal_tool_poses.num_goalset,
                    'lane_success': lane_success(None if result is None else result.success, owner.evidence_batch_size),
                    'fov_rejected_lanes': fov_rejected,
                    'max_attempts': kwargs.get('max_attempts', 5),
                    'success': result is not None and bool(result.success.any().item())})
                return result

        self.phases = None; self.single_phase = 'pose'; self.on_query = None; self.query_log = []
        self.evidence_batch_size = batch_size
        self.query_index = 0
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
            max_batch_size=batch_size, multi_env=False,
            num_ik_seeds=config.num_ik_seeds, num_trajopt_seeds=config.num_trajopt_seeds,
            max_goalset=config.max_candidates, self_collision_check=True,
            optimizer_collision_activation_distance=.01, use_cuda_graph=False,
            random_seed=config.seed, cost_manager_config_instance_type=manager_type(self.context))
        self.motion = NativeGraspPlanner(settings)
        # v0.8.0 MotionPlanner.attachment_manager points to an unimplemented
        # TrajOptSolver attribute. Use the tag's standalone native manager.
        from curobo._src.collision.attachment_manager import AttachmentManager
        self.attachment = AttachmentManager(self.motion.kinematics, self.motion.scene_collision_checker, self.args)
        if set(self.motion.joint_names) != set(self.names) or self.motion.tool_frames != [self.model.tcp_link]:
            raise ValueError('unexpected degrees of freedom or auxiliary pose targets')
        self.pad_target_query_range()
        # Validate the real model/frame convention before any physical action.
        self.check_fk()
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
        if not ik_only:
            self.motion.warmup(enable_graph=False, num_warmup_iterations=1)
        self.progress_ready = True
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
                started = time.monotonic()
                if _kind == 'trajopt':
                    trial_progress('Optimizing trajectory phase=%s', self.progress_phase)
                result = _original(*args, **kwargs)
                elapsed = time.monotonic() - started
                if elapsed >= 2.0:
                    trial_progress('Solver finished solver=%s phase=%s elapsed=%.1fs',
                                      _kind, self.progress_phase, elapsed)
                row = solver_evidence(result, _kind, self.progress_phase, self.query_index,
                                      len(self.solver_log) + 1, self.evidence_batch_size)
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
                return result
            solver.solve_pose = traced
        write_json(self.path/'planner_identity.json', {**self.identity, **coupling,
            'joint_names': list(self.names), 'tool_frames': self.motion.tool_frames,
            'default_plan_pose_attempts': 5, 'self_collision': True,
            'head_fov_enabled': config.head_fov_enabled, 'head_fov_margin': config.head_fov_margin,
            'head_fov_weight': config.head_fov_weight, 'head_fov_fk': 'position_basis_probes',
            'disabled_collision_links': [], 'target_contact_links': self.context['contact_links'],
            'handle_contact_links': self.context['handle_contact_links'],
            'handle_checker': self.context['handle_checker'] is not None})
        write_json(self.path/'mobile_base.json', {'reference_world': self.base.tolist(),
            'joint_bounds_local': bounds, 'workspace_center_world': list(center[:2]), 'workspace_radius_m': radius})
        trial_progress('Planner initialized side=%s elapsed=%.1fs', side, time.monotonic() - setup_started)

    def check_fk(self):
        fk = self.motion.compute_kinematics(self.state()).tool_poses.to_dict()[self.model.tcp_link].get_numpy_matrix().reshape(4, 4)
        actual = np.linalg.solve(self.base, tcp_pose(self.sim, self.side))
        translation_error = float(np.linalg.norm(fk[:3, 3] - actual[:3, 3]))
        angle_error = float(np.arccos(np.clip((np.trace(fk[:3, :3].T @ actual[:3, :3])-1)/2, -1, 1)))
        write_json(self.path/'fk_check.json', {'translation_error_m': translation_error, 'rotation_error_rad': angle_error,
            'scope':'initial calibration' if not self.sim.started else 'measured FK alignment with tracking residuals'})
        position_limit, rotation_limit = self.model.fk_limits
        if self.model.fk_check_required and (translation_error > position_limit or angle_error > rotation_limit):
            raise ValueError(f'V2/MuJoCo FK mismatch {translation_error}m/{angle_error}rad')

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
        # Native callbacks close over this owner. Break that cycle at station
        # release so solver tensors do not wait for Python's cyclic collector.
        self.motion = None
        self.attachment = None
        self.context.clear()
        self.sim = self.model.sim = None

    def matches_initial(self, sim):
        return (not sim.started and sim.model is self.initial_model
                and np.array_equal(sim.state_vector(), self.initial_state))

    def bind_trial(self, sim, config, path):
        """Reuse static geometry/solvers only at the identical frozen initial state."""
        self.detach()
        self.sim, self.model.sim, self.config, self.path = sim, sim, config, Path(path)
        self.phases, self.single_phase, self.on_query = None, 'pose', None
        self.progress_phase = 'pose'
        self.context['contact_allowed'] = False
        self.query_log.clear()
        self.solver_log.clear()
        self.fov_log.clear()
        if config.head_fov_enabled:
            self.fov_validator = SparseFOVValidator(
                sim, self.fov_definition, self.base_target_world, self.names,
                min_depth=config.head_fov_min_depth_m)
            self.context['fov_target'] = self.args.to_device(np.linalg.solve(
                self.base, np.r_[sim.data.xpos[sim.target_id], 1.])[:3])
        from curobo._src.cost.tool_pose_criteria import ToolPoseCriteria
        self.motion.update_tool_pose_criteria({self.motion.tool_frames[0]: ToolPoseCriteria()})
        reset_motion_seed(self.motion, config.seed)
        write_json(self.path / 'planner_reuse.json', {
            'resources_relative_to_run': str(self.resource_path.relative_to(self.path.parents[2])),
            'seed': config.seed, 'scope': 'identical frozen station, pre-close',
            'new_simulation': True, 'motion_replanned': True})

    def rebind_resources(self, sim, config, path, workspace):
        """Update measured locks, limits and world while retaining solver allocations."""
        from curobo._src.types.robot import RobotCfg

        self.detach()
        self.sim, self.config, self.path, self.workspace = sim, config, Path(path), workspace
        self.model = sim.robot.planning_model(sim, config, self.side, path, workspace)
        self.names, self.base = self.model.names, self.model.base
        self.reference_yaw, self.h_step_limit = self.model.reference_yaw, self.model.h_step_limit
        self.robot_cfg = self.model.cfg
        robot = RobotCfg.create(self.robot_cfg, device_cfg=self.args)
        parameters = robot.kinematics.kinematics_config
        for solver in (self.motion.ik_solver, self.motion.trajopt_solver):
            for rollout in solver.core.get_all_rollout_instances():
                rollout.transition_model.robot_model.update_kinematics_config(parameters)
        self.motion.kinematics.update_kinematics_config(parameters)
        lm = self.motion.ik_solver.seed_ik_solver
        if lm is not None:
            lm._robot_model.update_kinematics_config(parameters)
            lm._aux_robot_model.update_kinematics_config(parameters)
            margin = (lm.joint_limits.position[1] - lm.joint_limits.position[0]) * lm.config.joint_limit_margin
            lm.action_min.copy_(lm.joint_limits.position[0] + margin)
            lm.action_max.copy_(lm.joint_limits.position[1] - margin)
            lm.action_step_max.copy_(lm.config.max_step_size * (lm.action_max - lm.action_min).abs())
        if self.motion.graph_planner is not None:
            for rollout in self.motion.graph_planner.get_all_rollout_instances():
                rollout.transition_model.robot_model.update_kinematics_config(parameters)
            sampler = self.motion.graph_planner.sampling_strategy.action_sample_generator
            sampler.range_b.copy_(self.motion.graph_planner.action_bound_highs - self.motion.graph_planner.action_bound_lows)
            sampler.low_bounds.copy_(self.motion.graph_planner.action_bound_lows)
            sampler.up_bounds.copy_(self.motion.graph_planner.action_bound_highs)
        center, radius = workspace
        local = np.linalg.solve(self.base, np.r_[center[:2], self.base[2, 3], 1.])[:2]
        self.context.update(local_center=self.args.to_device(local), radius=radius,
                            movable_base=self.model.movable_base, contact_allowed=False)
        self.world_range = 2 * radius + 1.
        self.phases, self.single_phase, self.on_query = None, 'pose', None
        self.query_log.clear()
        self.solver_log.clear()
        self.fov_log.clear()
        self.initial_model, self.initial_state = sim.model, sim.state_vector().copy()
        if hasattr(self, '_carried_fit') and self._fit_model is not sim.model:
            del self._carried_fit
            self._fit_model = None
        self.refresh_world(self.path)
        self.check_fk()
        reset_motion_seed(self.motion, config.seed)
        write_json(self.path / 'planner_reuse.json', {'resource_path': str(self.resource_path),
            'solver_reused': True, 'measured_locks_and_world_updated': True,
            'reference_world': self.base.tolist(), 'joint_names': list(self.names)})

    def current_joints(self):
        return self.model.current_joints()

    def state(self, batch_size=1):
        from curobo.types import JointState
        values = ordered_points(self.current_joints(), list(self.names), self.motion.joint_names)
        return JointState.from_position(self.args.to_device(values).view(1, -1).repeat(batch_size, 1),
                                        joint_names=self.motion.joint_names)

    def goals(self, matrices):
        return self.batch_goals([matrices])

    def batch_goals(self, matrix_batches):
        from curobo.types import GoalToolPose
        values = np.array([[pose7(np.linalg.solve(self.base, matrix)) for matrix in matrices]
                           for matrices in matrix_batches])
        batch_size, count = values.shape[:2]
        return GoalToolPose(tool_frames=self.motion.tool_frames,
            position=self.args.to_device(values[..., :3]).view(batch_size, 1, 1, count, 3),
            quaternion=self.args.to_device(values[..., 3:]).view(batch_size, 1, 1, count, 4))

    def base_target_world(self, point):
        return self.model.base_target_world(point)

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
        write_json(self.path / 'grasp_inputs.json', {'seeds': [self.config.seed], 'world_goals': [matrices]})
        self.phases = iter(('goalset', 'pregrasp', 'grasp', 'lift')); self.on_query = on_query
        try:
            result = self.motion.plan_grasp(self.goals(matrices), self.state(),
                grasp_approach_offset=-GRASP_APPROACH_DISTANCE_M, grasp_lift_offset=.10,
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
                          'reference_world': self.base.tolist(),
                          'control_dt_s': 1 / self.sim.robot.config.control_hz,
                          'time_dilation': self.config.time_dilation,
                          'native_plan_grasp': True, 'native_full_plan_grasp': bool(lift), 'joint_names': list(self.names),
                          'phase_evidence': phase_evidence(self.query_log, self.solver_log, 0),
                          'solver_trace_ref': str((self.path / 'solver_trace.json').resolve()),
                          'grasp_inputs_ref': str((self.path / 'grasp_inputs.json').resolve()),
                          'head_fov': list(self.fov_log), 'finite_budget_not_impossibility': True,
                          'constrained_planning': self.config.head_fov_enabled,
                          'failure_reason': ('fov_constraint_failed' if any(not r['valid'] for r in self.fov_log)
                                             else 'constrained_planning_failure' if not success and self.config.head_fov_enabled else None)}
            write_json(self.path/'grasp_result.json', {**diagnostic, 'success': success, 'stages': stages})
            write_json(self.path/'solver_trace.json', self.solver_log)
            return success, index, stages, diagnostic
        finally:
            self.phases = None; self.on_query = None; self.context['contact_allowed'] = False
            self.detach()
            from curobo._src.cost.tool_pose_criteria import ToolPoseCriteria
            self.motion.update_tool_pose_criteria({self.motion.tool_frames[0]: ToolPoseCriteria()})

    def diagnostic_ik(self, matrices, *, seed, seeds_per_grasp):
        """One independent IK solve per grasp, all seeds returned, no TrajOpt or stepping."""
        count = len(matrices)
        sampler_seeds = [seed + index for index in range(count)]
        bind_batch_seeds(self.motion, sampler_seeds)
        self.progress_phase = 'diagnostic_ik'
        self.query_index = len(self.query_log) + 1
        self.context['contact_allowed'] = True
        started = time.monotonic()
        write_json(self.path / 'grasp_inputs.json', {'seeds': sampler_seeds, 'world_goals': matrices,
                                                   'purpose': 'diagnostic_ik_not_physical_trial'})
        try:
            result = self.motion.ik_solver.solve_pose(self.batch_goals([[matrix] for matrix in matrices]),
                current_state=static_ik_state(self.state(count)), return_seeds=seeds_per_grasp)
            evidence = ik_probe_evidence(result, count, seeds_per_grasp)
            if self.config.head_fov_enabled:
                success = np.asarray(evidence['per_seed_success'], bool)
                points = result.solution.detach().cpu().numpy()
                points = ordered_points(points, list(self.motion.joint_names), list(self.names))
                for grasp, sample in np.argwhere(success):
                    success[grasp, sample] = self.fov_validator.project(points[grasp, sample])['valid']
                evidence.update(per_seed_success=success.tolist(), feasible_count=int(success.sum()),
                    feasible_ratio=float(success.mean()) if evidence['status'] == 'measured' else None)
            evidence.update(sampler_seeds=sampler_seeds, planning_s=time.monotonic() - started,
                grasp_inputs_ref=str((self.path / 'grasp_inputs.json').resolve()),
                solver_trace_ref=str((self.path / 'solver_trace.json').resolve()))
            write_json(self.path / 'solver_trace.json', self.solver_log)
            write_json(self.path / 'ik_probe.json', evidence)
            return evidence
        finally:
            self.context['contact_allowed'] = False

    def grasp_batch(self, matrix_batches, seeds, on_query):
        """Same prepared world, independent seeds and pre-close results per lane."""
        bind_batch_seeds(self.motion, seeds)
        write_json(self.path / 'grasp_inputs.json', {'seeds': list(seeds), 'world_goals': matrix_batches})
        self.phases = iter(('goalset', 'pregrasp', 'grasp'))
        self.on_query = on_query
        started = time.monotonic()
        try:
            result = self.motion.plan_grasp(self.batch_goals(matrix_batches), self.state(len(seeds)),
                grasp_approach_offset=-GRASP_APPROACH_DISTANCE_M, plan_approach_to_grasp=True,
                plan_grasp_to_lift=False, disable_collision_links=[])
            elapsed = time.monotonic() - started
            lanes = []
            for slot, seed in enumerate(seeds):
                success = bool(result.success[slot].item())
                index = int(result.goalset_index[slot].reshape(-1)[0].item()) if result.goalset_index is not None else None
                stages = {}
                if success:
                    for name, field in (('pregrasp', 'approach'), ('approach', 'grasp')):
                        trajectory = getattr(result, field + '_interpolated_trajectory')[slot:slot + 1]
                        last = getattr(result, field + '_interpolated_last_tstep')
                        stages[name] = self.points(trajectory, None if last is None else last[slot:slot + 1])
                diagnostic = {'status': result.status, 'seed': seed, 'batch_slot': slot,
                              'phase_evidence': phase_evidence(self.query_log, self.solver_log, slot),
                              'solver_trace_ref': str((self.path / 'solver_trace.json').resolve()),
                              'grasp_inputs_ref': str((self.path / 'grasp_inputs.json').resolve()),
                              'batch_seeds': list(seeds), 'queries': list(self.query_log),
                              'planning_s': elapsed, 'native_max_attempts': self.config.max_attempts,
                              'joint_names': list(self.names), 'reference_world': self.base.tolist(),
                              'control_dt_s': 1 / self.sim.robot.config.control_hz,
                              'time_dilation': self.config.time_dilation, 'multi_env': False}
                lanes.append({'success': success, 'index': index, 'stages': stages,
                              'diagnostics': diagnostic})
            write_json(self.path / 'batch_grasp_result.json', lanes)
            write_json(self.path / 'solver_trace.json', self.solver_log)
            return lanes
        finally:
            self.phases = None
            self.on_query = None
            self.context['contact_allowed'] = False
            self.detach()
            from curobo._src.cost.tool_pose_criteria import ToolPoseCriteria
            self.motion.update_tool_pose_criteria({self.motion.tool_frames[0]: ToolPoseCriteria()})

    def detach(self):
        params = self.motion.kinematics.config.kinematics_config
        params.reset_link_spheres(self.model.attached_link)
        self.context['attached'] = False
        self.context['fov_attached_offset'] = None
        if self.fov_validator is not None:
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
        target_pose = np.linalg.solve(self.base, body_pose(self.sim, self.sim.target_id))
        reused = hasattr(self, '_carried_fit') and self._fit_model is self.sim.model
        if not reused:
            mesh = self.context['target_checker'].scene_model.mesh[0]
            vertices = (np.asarray(mesh.vertices) - target_pose[:3, 3]) @ target_pose[:3, :3]
            geometry = trimesh.Trimesh(vertices=vertices, faces=mesh.faces, process=False)
            self._carried_fit = fit_spheres_to_mesh(geometry, num_spheres=40,
                fit_type=SphereFitType.VOXEL, compute_metrics=True, device_cfg=self.args)
            self._fit_model = self.sim.model
        fit = self._carried_fit
        if fit.num_spheres == 0:
            raise ValueError('empty carried-object fit')
        transform = self.args.to_device(target_pose)
        centers = fit.centers @ transform[:3, :3].T + transform[:3, 3]
        spheres = torch.cat((centers, fit.radii[:, None]), dim=-1)
        state = self.state() if state is None else state
        self.attachment.update(spheres, state, link_name=self.model.attached_link,
            world_objects_pose_offset=Pose.from_list([0,0,0,1,0,0,0], device_cfg=self.args))
        self.context['attached'] = True
        if self.config.head_fov_enabled:
            tcp = self.motion.compute_kinematics(state).tool_poses.get_link_pose(self.model.tcp_link, make_contiguous=True).get_matrix()
            target = self.context['fov_target']
            offset = (tcp[..., :3, :3].transpose(-1, -2) @ (target-tcp[..., :3, 3])[..., None]).squeeze(-1)
            self.context['fov_attached_offset'] = offset.detach().reshape(3)
            self.fov_validator.attached_offset = offset.detach().cpu().numpy().reshape(3)
        info = {'physics_attachment':False, 'sphere_count':fit.num_spheres,
                'fit_frame': 'target_body_local', 'fit_reused': reused,
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
        for checker in (self.context['target_checker'], self.context['handle_checker']):
            if checker is None:
                continue
            for data in checker.data.get_valid_data():
                if hasattr(data, 'dims'):
                    data.dims[..., :3].clamp_(min=minimum_dimension)
        write_json(self.path/'target_query_range.json', {'largest_robot_sphere_radius_m':max_radius,
            'minimum_query_bound_dimension_m':minimum_dimension, 'geometry_changed':False,
            'reason':'no-hit SDF must exceed every queried sphere radius plus activation margin'})

    def refresh_world(self, path):
        import torch
        torch.cuda.synchronize()
        # No graph capture: no outstanding queries may refer to discarded BVHs.
        checkers = [self.motion.scene_collision_checker, self.context['target_checker']]
        if self.context['handle_checker'] is not None:
            checkers.append(self.context['handle_checker'])
        for checker in checkers:
            for data in checker.data.get_valid_data():
                if hasattr(data, 'wp_cache'): data.wp_cache.clear()
        self.motion.update_world(collision_world(self.sim, Path(path), self.world_range, reference_base=self.base))
        self.context['target_checker'].load_collision_model(collision_world(
            self.sim, Path(path), self.world_range, reference_base=self.base, target_only=True,
            bodies=self.target_bodies - self.handle_bodies))
        if self.context['handle_checker'] is not None:
            self.context['handle_checker'].load_collision_model(collision_world(
                self.sim, Path(path), self.world_range, reference_base=self.base, target_only=True,
                bodies=self.handle_bodies, filename='handle_world.json'))
        self.pad_target_query_range()
        if self.config.head_fov_enabled:
            self.context['fov_target'] = self.args.to_device(np.linalg.solve(
                self.base, np.r_[self.sim.data.xpos[self.sim.target_id], 1.])[:3])
            self.fov_validator.target_world = self.sim.data.xpos[self.sim.target_id].copy()

    def plan(self, goal):
        result = self.motion.plan_pose(self.goals([goal]), self.state())
        write_json(self.path / 'solver_trace.json', self.solver_log)
        success = result is not None and bool(result.success.any().item())
        points = self.points(result.get_interpolated_plan()) if success else ()
        fov_failed = result is not None and getattr(result, 'status', None) == 'FOV_CONSTRAINT_FAILED'
        return PlanResult('success' if points else 'fov_constraint_failed' if fov_failed else 'no_solution', self.names, points,
            {'status': 'success' if points else 'FOV_CONSTRAINT_FAILED' if fov_failed else 'V2_POSE_FAIL',
             'reference_world': self.base.tolist(), 'control_dt_s': 1 / self.sim.robot.config.control_hz,
             'time_dilation': self.config.time_dilation,
             'failure_reason': ('fov_constraint_failed' if fov_failed else
                                'constrained_planning_failure' if not points and self.config.head_fov_enabled else None),
             'head_fov': list(self.fov_log), 'queries': list(self.query_log),
             'phase_evidence': phase_evidence(self.query_log, self.solver_log, 0),
             'solver_trace_ref': str((self.path / 'solver_trace.json').resolve()),
             'finite_budget_not_impossibility': True, 'constrained_planning': self.config.head_fov_enabled})
