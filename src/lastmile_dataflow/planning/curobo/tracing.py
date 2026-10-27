"""Native planner query tracing and solver evidence for V2Planner.

Both hooks receive the owning V2Planner explicitly; neither captures planner
state through closures, so they can be constructed and tested in isolation.
"""
import time

from ...utils.logging_config import trial_progress
from ..evidence import lane_success, solver_evidence
from ..robots.rby1m_mobile_torso import static_ik_state, chain_state

CONTACT_PHASES = ('goalset', 'grasp', 'approach_contact')


class TracedPlanPose:
    """Mixin over cuRobo (Batch)MotionPlanner; `owner` is assigned after construction."""
    owner = None

    def plan_pose(self, goal_tool_poses, current_state, *args, **kwargs):
        owner = self.owner
        phase = next(owner.phases) if owner.phases is not None else owner.single_phase
        owner.progress_phase = phase
        owner.query_index = len(owner.query_log) + 1
        if owner.on_query is not None:
            owner.on_query(phase)
        kwargs.setdefault('max_attempts', owner.config.max_attempts)
        owner.context.contact_allowed = phase in CONTACT_PHASES
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


_PLANNER_TYPES = {}


def native_grasp_planner_type(native_type):
    """The traced subclass of the selected native planner, created once per base type."""
    if native_type not in _PLANNER_TYPES:
        _PLANNER_TYPES[native_type] = type('NativeGraspPlanner', (TracedPlanPose, native_type), {})
    return _PLANNER_TYPES[native_type]


class SolverTrace:
    """Replaces one solver's solve_pose; observes native solves, issues no extra search."""

    def __init__(self, owner, solver, kind):
        self.owner, self.solver, self.kind = owner, solver, kind
        self.original = solver.solve_pose

    @classmethod
    def install(cls, owner, solver, kind):
        solver.solve_pose = cls(owner, solver, kind)

    def __call__(self, *args, **kwargs):
        owner = self.owner
        if self.kind == 'ik' and kwargs.get('current_state') is not None:
            # Native grasp passes the previous trajectory's sample dt
            # into static IK. CSpacePosition then tightens goal bounds
            # to a SINGLE sample's velocity reach (e.g. 25 ms), making
            # an 8 cm grasp approach falsely infeasible. Static endpoint
            # IK has no one-tick duration; trajectory optimization and
            # real control retain all velocity/acceleration limits.
            kwargs['current_state'] = static_ik_state(kwargs['current_state'])
        started = time.monotonic()
        if self.kind == 'trajopt':
            trial_progress('Optimizing trajectory phase=%s', owner.progress_phase)
        result = self.original(*args, **kwargs)
        elapsed = time.monotonic() - started
        if elapsed >= 2.0:
            trial_progress('Solver finished solver=%s phase=%s elapsed=%.1fs',
                           self.kind, owner.progress_phase, elapsed)
        row = solver_evidence(result, self.kind, owner.progress_phase, owner.query_index,
                              len(owner.solver_log) + 1, owner.evidence_batch_size)
        for field in ('position_error', 'rotation_error', 'feasible'):
            value = getattr(result, field, None)
            if value is not None: row[field] = value.detach().cpu().tolist()
        metrics = getattr(result, 'metrics', None)
        if metrics is None and self.kind == 'ik' and result.optimized_seeds is not None:
            # Evaluation only, using already optimized seeds: no new search.
            metrics = self.solver.core.metrics_rollout.compute_metrics_from_action(
                result.optimized_seeds.reshape(-1, 1, len(owner.names)))
        if metrics is not None and metrics.costs_and_constraints is not None:
            row['constraint_terms'] = self.constraint_terms(result, metrics)
        owner.solver_log.append(row)
        return result

    def constraint_terms(self, result, metrics):
        kinematics = self.owner.motion.kinematics
        rows = {}
        for category in ('constraints', 'hybrid_costs_constraints'):
            terms = getattr(metrics.costs_and_constraints, category)
            for name, value in zip(terms.names, terms.values):
                row = rows[category + '/' + name] = {
                    'min': float(value.min().item()), 'max': float(value.max().item()),
                    'positive_count': int((value > 0).sum().item())}
                if value.ndim == 3 and value.shape[-1] == kinematics.total_spheres:
                    ids = result.debug_info.get('seed_idx')
                    if ids is not None:
                        row['selected_link_costs'] = self.link_costs(value, ids, kinematics.config.kinematics_config)
        return rows

    @staticmethod
    def link_costs(value, ids, params):
        selected = value[ids.reshape(-1), 0].detach().cpu().numpy()
        links = {}
        for link in params.link_name_to_idx_map:
            sphere_ids = params.get_sphere_index_from_link_name(link).cpu().numpy()
            costs = selected[:, sphere_ids].sum(axis=-1)
            if (costs > 0).any(): links[link] = costs.tolist()
        return links
