"""Native grasp planning and real 20D execution; no scene edits or state resets."""
import copy
import numpy as np
from ..io import write_json
from ..planning.curobo import body_pose, tcp_pose
from ..planning.curobo_v2 import V2Planner
from ..validation.pick import PickMonitor
from ..validation.no_edit import evaluate_mobile_pick, evaluate_open


def candidate_pool(ctx, candidates, seed, forced):
    rng = np.random.default_rng(seed)
    if forced:
        pool = [c for c in candidates if c['row'] == forced['grasp_row'] and c['source'] == forced['grasp_source']]
        if not pool:
            raise ValueError('missing winning grasp')
        return pool[:ctx.config.max_grasp_candidates]
    base = ctx.sim.robot.group('base')
    def score(c):
        goal = body_pose(ctx.sim, ctx.sim.model.body(c['body']).id) @ c['pose_local']
        vector = goal[:3, 3] - np.r_[base[:2], goal[2, 3]]
        return float(np.dot(goal[:3, 2], vector) / max(np.linalg.norm(vector), 1e-9))
    pool = sorted(candidates, key=score, reverse=True)[:max(ctx.config.max_grasp_candidates * 8, 16)]
    return [pool[i] for i in rng.permutation(len(pool))[:ctx.config.max_grasp_candidates]]


def manipulate_v2(ctx, task, candidates, assets_dir, seed, forced=None):
    from .no_edit_execution import OperationFailure, planner_options
    pool = candidate_pool(ctx, candidates, seed, forced)
    sides = [forced['arm']] if forced else (['left', 'right'] if seed % 2 == 0 else ['right', 'left'])
    sim = ctx.sim
    options = planner_options(ctx.config, assets_dir, seed)
    planner = None
    def query(phase):
        ctx.deadline()
        if ctx.plan_count >= ctx.max_plans:
            raise OperationFailure('planning_budget_no_solution')
        ctx.plan_count += 1
        ctx.phase = 'plan_' + phase
        ctx.plans.append({'phase': ctx.phase, 'query_index': ctx.plan_count,
                          'planner': 'curobo_v2_v080', 'native_max_attempts': 5})
        write_json(ctx.recorder.path / 'planning.json', ctx.plans)
    try:
        diagnostic = None
        for side in sides:
            ctx.deadline()
            directory = ctx.recorder.path / f'v080-{ctx.plan_count}-{side}'
            directory.mkdir()
            planner = V2Planner(sim, options, side, directory, workspace=(task['anchor_world'], ctx.config.radius_m))
            goals = [body_pose(sim, sim.model.body(c['body']).id) @ c['pose_local'] for c in pool]
            for goal in goals:
                goal[:3, 3] += goal[:3, 2] * ctx.config.approach_offset_m
            if not goals:
                raise OperationFailure('no_grasp_candidates')
            if task['operation'] == 'pick':
                success, index, stages, diagnostic = planner.grasp(goals, query)
            else:
                # A door handle is not lifted. Still use the native goal-set
                # approach/grasp pipeline, with lift explicitly disabled.
                success, index, stages, diagnostic = planner.grasp(goals, query, lift=False)
            ctx.deadline()
            ctx.plans.append({'phase': 'native_grasp_result', **diagnostic})
            if success:
                break
            planner.close(); planner = None
        else:
            raise OperationFailure('planning_no_solution', diagnostic)
        c = pool[index]
        goal = goals[index]
        pre = goal.copy(); pre[:3, 3] -= goal[:3, 2] * .08
        ctx.monitor = PickMonitor(sim, side); ctx.fingers = ctx.monitor.fingers; ctx.samples = []
        ctx.monitor.initial.update(workspace_center_xy=task['anchor_world'][:2], workspace_radius_m=ctx.config.radius_m)
        initial = copy.deepcopy(ctx.monitor.initial)
        ctx.manip_base_target = sim.robot.group('base').copy()
        if task['operation'] == 'open':
            j = sim.model.joint(task['joint_name']).id
            ctx.open_joint = int(sim.model.jnt_qposadr[j])
        ctx.begin()
        ctx.phase = 'pregrasp'
        ctx.follow_points(planner, stages['pregrasp'], side, -.05, 0., pre)
        ctx.phase = 'approach'
        ctx.follow_points(planner, stages['approach'], side, -.05, 0., goal)
        ctx.phase = 'close'; q = sim.robot.group(side + '_arm').copy()
        h = float(sim.robot.group('torso')[1])
        for _ in range(20):
            ctx.arm_tick(side, q, 0., h)
        # Rebuild at measured closed state: relative finger locks must not retain
        # their open value, and the lift must start at the actual executed state.
        planner.close(); planner = None
        directory = ctx.recorder.path / f'v080-closed-{ctx.plan_count}-{side}'
        directory.mkdir()
        planner = V2Planner(sim, options, side, directory, workspace=(task['anchor_world'], ctx.config.radius_m))
        if task['operation'] == 'pick':
            write_json(ctx.recorder.path / 'attached_object.json', planner.attach_target_geometry())
            ctx.phase = 'lift_actual'
            lift = tcp_pose(sim, side).copy(); lift[2, 3] += .10
            ctx.follow(planner, lift, side, 0., h)
            ctx.phase = 'hold'; q = sim.robot.group(side + '_arm').copy(); h = float(sim.robot.group('torso')[1])
            for _ in range(50):
                ctx.arm_tick(side, q, 0., h)
            verdict = evaluate_mobile_pick(ctx.samples, initial)
        else:
            if not ctx.samples or len(ctx.samples[-1]['finger_forces_n']) != 2:
                raise OperationFailure('open_no_grip')
            planner.single_phase = 'approach_contact'
            step = ctx.config.open_waypoint_step_m if task['joint_kind'] == 'slide' else ctx.config.open_waypoint_step_rad
            count = int(abs(task['joint_goal'] - task['joint_initial']) / step) * 3 + 12
            direction = np.sign(task['joint_goal'] - task['joint_initial'])
            for index in range(count):
                remaining = direction * (task['joint_goal'] - float(sim.data.qpos[ctx.open_joint]))
                if remaining <= 1e-3:
                    break
                delta = direction * min(step, remaining + .002)
                tcp = tcp_pose(sim, side).copy(); axis = sim.data.xaxis[j].copy()
                if task['joint_kind'] == 'slide':
                    tcp[:3, 3] += axis * delta
                else:
                    skew = np.array([[0,-axis[2],axis[1]], [axis[2],0,-axis[0]], [-axis[1],axis[0],0]])
                    rotation = np.eye(3) + np.sin(delta) * skew + (1 - np.cos(delta)) * (skew @ skew)
                    origin = sim.data.xanchor[j]
                    tcp[:3, 3] = origin + rotation @ (tcp[:3, 3] - origin); tcp[:3, :3] = rotation @ tcp[:3, :3]
                world = directory / f'world-{index}'; world.mkdir(); planner.refresh_world(world)
                ctx.phase = 'open'; ctx.follow(planner, tcp, side, 0., h)
                h = float(sim.robot.group('torso')[1])
                if len(ctx.samples[-1]['finger_forces_n']) != 2:
                    raise OperationFailure('open_contact_lost')
            ctx.phase = 'open_hold'; q = sim.robot.group(side + '_arm').copy()
            for _ in range(int(np.ceil((ctx.config.open_hold_s + .1) * sim.robot.config.control_hz))):
                ctx.arm_tick(side, q, 0., h)
            verdict = evaluate_open(task, ctx.samples, ctx.config.open_hold_s)
        control = {'arm': side, 'torso_h': float(sim.robot.group('torso')[1]), 'torso_mode': 'continuous_mimic',
                   'grasp_row': c['row'], 'grasp_source': c['source'], 'seed': seed}
        write_json(ctx.recorder.path / f'verdict-{len(ctx.plans)}.json', verdict)
        return verdict, control
    finally:
        if planner is not None:
            planner.detach()
            planner.close()
