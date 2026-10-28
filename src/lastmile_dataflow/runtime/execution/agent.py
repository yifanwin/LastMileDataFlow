"""Code-agent episode on one project Simulation through a RoboCasa-native interface.

The agent (PyRUA) composes its own primitives; this module owns the episode:
one Simulation, one AttemptRecorder, per-physics-tick project evidence and the
project verdict. `AgentEnvironment` exposes what RPent's RoboCasa primitives
read from a robosuite env (raw observation keys, 12-D composite actions,
cameras, depth and world maps) and maps them onto the project robot.
"""
from dataclasses import asdict
import math
import queue
import threading
import time
import traceback

import mujoco
import numpy as np

from .context import clone_sim
from .lifecycle import classify_episode_exception, episode_phase, isolated_episode, mark_episode_phase, track_resource
from .vla import OperationEvidence
from ..control.action import InvalidAction
from ..outcomes.contacts import CONTACT_POLICY, SIMULATION_FAILURES
from ..outcomes.pick import PROTOCOL as PICK_PROTOCOL
from ..outcomes.vla import PROTOCOL
from ...data.recorder import AttemptRecorder, open_trace
from ...data.rendering.camera_calibration import rendered_camera
from ...policies.task import instruction
from ...scenes.geometry import descendants
from ...scenes.stations.sampling import place_frozen_station
from ...utils.io import write_json

AGENT_PROTOCOL = 'pyrua-native-agent-v1'
# robosuite RLinf fork (rpent branch), omron_mobile_base.xml: base-mounted
# forward-down floor camera. Project snapshots predate it, so it is rendered
# from the same body pose as a render-only free camera; the MJCF is unchanged.
NAVVIEW = {'body': 'mobilebase0_base', 'pos': (.2, 0., 1.6), 'xyaxes': (0., -1., 0., .643, 0., .766), 'fovy': 75.}
NAVVIEW_NAME = 'mobilebase0_navview'
FEEDBACK = ('verdict', 'success_only')


class EpisodeStopped(RuntimeError):
    """The project ended the episode; no further motion is executed."""


class OwnerThread:
    """Run simulator and renderer calls on the thread that owns the EGL context.

    The agent's tool calls arrive on server worker threads; MuJoCo rendering
    must stay on one thread, as RPent's env server keeps it on its main thread.
    """

    def __init__(self):
        self.owner = threading.get_ident()
        self.requests = queue.Queue()

    def call(self, fn, *args, **kwargs):
        if threading.get_ident() == self.owner:
            return fn(*args, **kwargs)
        done, box = threading.Event(), {}
        self.requests.put((fn, args, kwargs, done, box))
        done.wait()
        if 'error' in box:
            raise box['error']
        return box['value']

    def serve(self, worker):
        """Execute requests until `worker` has finished."""
        while worker.is_alive() or not self.requests.empty():
            try:
                fn, args, kwargs, done, box = self.requests.get(timeout=.05)
            except queue.Empty:
                continue
            try:
                box['value'] = fn(*args, **kwargs)
            except BaseException as exc:
                box['error'] = exc
            finally:
                done.set()


def _rotation(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s], [s, c]])


def native_to_project(native, grip):
    """robosuite PandaOmron composite action -> project robocasa-v1 action (base x/y left as given).

    Layout is shared: arm delta [0:6], gripper [6], base [7:10], torso [10],
    base mode [11]. robosuite moves the Panda gripper by sign(a[6]) (0 keeps
    the command) and selects base mode by a[11] > 0; the project adapter
    thresholds both at 0.5. Returns (project action, held gripper command).
    """
    a = np.asarray(native, dtype=float).reshape(-1)
    if a.shape != (12,) or not np.isfinite(a).all():
        raise InvalidAction('native action must be 12 finite values')
    grip = 1. if a[6] > 0 else 0. if a[6] < 0 else grip
    result = np.clip(a, -1., 1.)
    result[6] = grip
    result[11] = 1. if a[11] > 0 else 0.
    return result, grip


def native_base_xy(command, yaw, initial_yaw, deadband):
    """Project base x/y command reproducing robosuite's steady base velocity.

    robosuite rotates the heading-frame command by the yaw change into joints
    aligned with the episode's initial heading; each joint has a Coulomb
    deadband. Project joints are world aligned, so the same command would be
    clipped along world axes instead (measured: forward drove along world +x
    from a 10 deg station). Steady robosuite velocity in the world is
    reproduced and the project axes' deadband is compensated, as the adapter's
    reference tracking does. Identity when the two joint frames coincide.
    """
    theta = yaw - initial_yaw
    joint = _rotation(theta) @ np.clip(command, -1., 1.)
    steady = np.sign(joint)*np.maximum(np.abs(joint)-deadband, 0.)
    world = _rotation(initial_yaw) @ steady
    world = world + np.sign(world)*deadband
    return _rotation(-theta) @ world


def world_map(depth, calibration, far_m):
    """Per-pixel world xyz, row 0 at the image top; NaN where depth is invalid."""
    (fx, _, cx), (_, fy, cy), _ = calibration['intrinsics']['K']
    transform = np.asarray(calibration['T_world_camera_optical'])
    rows, cols = np.indices(depth.shape)
    points = np.stack([(cols-cx)/fx*depth, (rows-cy)/fy*depth, depth], axis=-1)
    world = points @ transform[:3, :3].T + transform[:3, 3]
    world[(depth <= 0) | (depth >= far_m*(1-1e-4)) | ~np.isfinite(depth)] = np.nan
    return world


def _xyzw(matrix):
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.asarray(matrix).reshape(9))
    return quat[[1, 2, 3, 0]]


class AgentEnvironment:
    """RoboCasa-native view of one running project episode.

    Methods must run on `owner`'s thread (callers marshal through `owner.call`).
    `stop` is set once the project
    ends the episode (task verdict, constraint, simulation failure or budget);
    after that only observation calls succeed.
    """

    def __init__(self, sim, task, evidence, *, owner, deadline, max_steps, feedback='verdict', hold_s=1.):
        if feedback not in FEEDBACK:
            raise ValueError('unknown task feedback mode')
        self.sim, self.task, self.evidence, self.owner = sim, task, evidence, owner
        self.deadline, self.max_steps, self.feedback, self.hold_s = deadline, max_steps, feedback, hold_s
        self.language = instruction(task)
        self.steps = 0
        self.stop = None
        self.renderers = {}
        robot = sim.robot
        ns = robot.config.namespace
        self.cameras = {name: ns+name for name in ('robot0_agentview_left', 'robot0_agentview_right', 'robot0_eye_in_hand')}
        for name in self.cameras.values():
            sim.model.camera(name)
        self.base_body = sim.model.body(ns+NAVVIEW['body']).id
        self.grip = float(robot.control['action'][6])
        # Base joints are world aligned (identity attach frame); the adapter rotates by yaw - initial_yaw.
        self.initial_yaw = float(robot.control['initial_yaw'])
        ids = [robot.act_ids[name] for name in robot.actuators['base']]
        self.base_deadband = (sim.model.dof_frictionloss[robot.dofs['base']] / sim.model.actuator_gainprm[ids, 0])[:2]
        self.fingers = tuple(set(group) for group in robot.finger_bodies('right'))
        self.objects = [(row['instance_id'], descendants(sim.model, row['body_id'])) for row in sim.catalog]

    # -- observation -------------------------------------------------------

    def observation(self):
        sim = self.sim
        state = sim.robot.record_state()
        center = sim.data.site_xpos[sim.robot.base_center_id]
        return {'robot0_eef_pos': sim.data.site_xpos[sim.robot.tcp_site('right')].copy(),
                'robot0_eef_quat': _xyzw(sim.data.xmat[sim.robot.hand_id]),
                'robot0_gripper_qpos': state[14:16].copy(),
                'robot0_base_pos': center.copy(),
                'robot0_base_quat': state[10:14].copy(),
                'robot0_base_to_eef_pos': state[0:3].copy(),
                'robot0_base_to_eef_quat': state[3:7].copy(),
                'language': self.language}

    def _renderer(self, height, width):
        key = (height, width)
        if key not in self.renderers:
            self.renderers[key] = mujoco.Renderer(self.sim.model, height=height, width=width)
        return self.renderers[key]

    def _navview_camera(self):
        rotation = self.sim.data.xmat[self.base_body].reshape(3, 3)
        x = np.asarray(NAVVIEW['xyaxes'][:3]); y = np.asarray(NAVVIEW['xyaxes'][3:])
        x /= np.linalg.norm(x); y -= x*(x@y); y /= np.linalg.norm(y)
        forward = rotation @ -np.cross(x, y)
        position = self.sim.data.xpos[self.base_body] + rotation @ np.asarray(NAVVIEW['pos'])
        camera = mujoco.MjvCamera()
        camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        camera.distance = 1.
        camera.lookat[:] = position + forward
        camera.azimuth = math.degrees(math.atan2(forward[1], forward[0]))
        camera.elevation = math.degrees(math.asin(np.clip(forward[2], -1., 1.)))
        return camera

    def sensor(self, camera, height, width, *, depth=False):
        """RGB (top row first), optional metric depth and the actual GL calibration."""
        renderer = self._renderer(height, width)
        visual = self.sim.model.vis.global_
        fovy = float(visual.fovy)
        try:
            if camera == NAVVIEW_NAME:
                visual.fovy = NAVVIEW['fovy']   # free cameras use the global fovy; render-only
                renderer.update_scene(self.sim.data, camera=self._navview_camera())
            else:
                renderer.update_scene(self.sim.data, camera=self.cameras[camera])
            rgb = renderer.render().copy()
            calibration = rendered_camera(renderer, width, height)
            distance = None
            if depth:
                renderer.enable_depth_rendering()
                try:
                    distance = renderer.render().copy()
                finally:
                    renderer.disable_depth_rendering()
        finally:
            visual.fovy = fovy
        return rgb, distance, calibration

    def render(self, camera, height, width, depth=False):
        rgb, distance, _ = self.sensor(camera, height, width, depth=depth)
        return (rgb, distance) if depth else rgb

    def camera_meta(self, camera, height, width):
        _, _, calibration = self.sensor(camera, height, width)
        return {'camera_name': camera, 'height': height, 'width': width,
                'intrinsic': calibration['intrinsics']['K'],
                'extrinsic_cam2world': calibration['T_world_camera_optical'],
                'depth_near': calibration['near_m'], 'depth_far': calibration['far_m'],
                'source': 'render_only_free_camera' if camera == NAVVIEW_NAME else 'mjcf_camera'}

    def world_map(self, camera, height, width):
        _, distance, calibration = self.sensor(camera, height, width, depth=True)
        return world_map(distance, calibration, calibration['far_m'])

    def grasp_contact(self):
        """Both finger groups touch the same scene instance (robosuite `_check_grasp` analogue)."""
        touched = [set(), set()]
        model = self.sim.model
        for contact in self.sim.data.contact:
            bodies = int(model.geom_bodyid[contact.geom1]), int(model.geom_bodyid[contact.geom2])
            for side, finger in enumerate(self.fingers):
                for a, b in (bodies, bodies[::-1]):
                    if a in finger:
                        touched[side].add(b)
        for name, bodies in self.objects:
            if touched[0] & bodies and touched[1] & bodies:
                return True, name
        return False, None

    # -- task feedback -----------------------------------------------------

    def verdict(self):
        return self.evidence.verdict()

    def success(self):
        return self.evidence.verdict()['status'] == 'success'

    def progress(self):
        if self.feedback == 'success_only':
            return {}
        verdict = self.evidence.verdict()
        hand = next(iter(verdict['hands'].values()))
        if self.task['operation'] == 'pick':
            return {'held_s': round(float(hand.get('tail_held_s', 0.)), 3),
                    'required_hold_s': PICK_PROTOCOL['hold_s'],
                    'max_lift_m': round(float(hand.get('max_lift_m', 0.)), 4),
                    'required_lift_m': PICK_PROTOCOL['lift_m']}
        required = abs(self.task['joint_goal']-self.task['joint_initial'])
        return {'progress': round(float(hand.get('final_progress', 0.)), 4), 'required_progress': round(required, 4),
                'held_s': round(float(hand.get('tail_hold_s', 0.)), 3), 'required_hold_s': self.hold_s}

    def success_criteria(self):
        radius = self.evidence.primary.initial['workspace_radius_m']
        if self.task['operation'] == 'pick':
            goal = (f"- Success: the object named by the instruction is held between BOTH fingers "
                    f"(positive contact force on each finger), lifted at least {PICK_PROTOCOL['lift_m']} m above "
                    f"its initial height, touching no supporting surface, continuously for "
                    f"{PICK_PROTOCOL['hold_s']} s of simulated time (20 env steps = 1 s). A slip of more than "
                    f"{PICK_PROTOCOL['relative_translation_m']} m or {np.rad2deg(PICK_PROTOCOL['relative_rotation_rad']):.0f} deg "
                    "relative to the gripper restarts the hold window.")
        else:
            unit = 'm' if self.task['joint_kind'] == 'slide' else 'rad'
            goal = (f"- Success: the {('drawer' if self.task['joint_kind'] == 'slide' else 'door')} named by the "
                    f"instruction is opened by at least {abs(self.task['joint_goal']-self.task['joint_initial']):.3f} {unit} "
                    f"from its initial position while both fingers stay in contact, continuously for {self.hold_s} s "
                    "of simulated time.")
        return '\n'.join([
            '# Success condition of this LastMileDataFlow task (judged by the project from physics)',
            goal,
            f'- The mobile base must stay within {radius} m (x/y) of the task area for the whole episode; '
            'leaving it ends the episode as a failure.',
            '- Non-finite physics or MuJoCo warnings end the episode as a simulation failure.',
            '- The episode ends at the first moment the condition holds; later motion calls are refused.',
            '- Object poses are NOT given: localize everything from the cameras and world maps.',
            '- `task_progress` reports the live values of this check.' if self.feedback == 'verdict'
            else '- `task_progress` is empty in this run; only the success flag is reported.'])

    # -- motion --------------------------------------------------------------

    def step(self, native):
        if self.stop is not None:
            raise EpisodeStopped(f"episode over ({self.stop['status']}: {self.stop['reason']}); call finish")
        if time.monotonic() >= self.deadline:
            self.stop = {'status': 'timeout', 'reason': 'attempt_wall_clock_budget'}
            raise EpisodeStopped('attempt wall-clock budget exhausted; call finish')
        if self.steps >= self.max_steps:
            self.stop = {'status': 'failure', 'reason': 'control_step_budget_exhausted'}
            raise EpisodeStopped('control step budget exhausted; call finish')
        action, self.grip = native_to_project(native, self.grip)
        action[7:9] = native_base_xy(action[7:9], float(self.sim.robot.base_pose()[2]),
                                     self.initial_yaw, self.base_deadband)
        self.evidence.failures = []
        _, stop = self.sim.step(action, substep_check=self.evidence.check, command_raw=np.asarray(native, float))
        self.steps += 1
        if stop:
            reason = self.evidence.failures[0]
            self.stop = {'status': 'simulation_failure' if reason in SIMULATION_FAILURES else 'failure', 'reason': reason}
        else:
            verdict = self.evidence.verdict()
            if verdict['status'] in ('success', 'infrastructure_error', 'simulation_failure'):
                self.stop = {'status': verdict['status'], 'reason': verdict['reason']}
        return self.observation()

    def close(self):
        for renderer in self.renderers.values():
            renderer.close()
        self.renderers.clear()


@isolated_episode
def run_agent_attempt(baseline, task, station, config, collection, *, root, attempt_id, seed, policy):
    """One agent episode from a frozen station; the project verdict decides the task outcome."""
    sim = clone_sim(baseline, target=task['target_body'], handle=task.get('handle_body')); place_frozen_station(sim, station)
    if config.third_person_enabled:
        sim.enable_third_person()
    packet = {'schema_version': 'pyrua-station-v1', 'robot': asdict(sim.robot.config),
              'collection': asdict(collection), 'task': task, 'station': station,
              'protocol': asdict(config), 'contact_policy': CONTACT_POLICY, 'seed': seed,
              'policy': asdict(policy.config), 'model_service': policy.metadata,
              'scene': getattr(baseline, 'scene_id', None)}
    recorder = AttemptRecorder(root, packet, attempt_id=attempt_id)
    recorder.bind_simulation(sim, width=config.width, height=config.height)
    physics = track_resource(open_trace(recorder.path/'physics.jsonl.gz', collection.record_physics))
    evidence = OperationEvidence(sim, task, config, physics, phase='agent_operation')
    status = 'infrastructure_error'; reason = 'unfinished'; exception_detail = None; agent = None
    environment = None; failure_scope = None
    started = time.monotonic()
    try:
        recorder.phase = 'agent_operation'
        mark_episode_phase('execution')
        sim.begin()
        write_json(recorder.path/'initial_state.json', sim.observe_state())
        environment = AgentEnvironment(sim, task, evidence, owner=OwnerThread(),
                                       deadline=started+config.attempt_timeout_s,
                                       max_steps=policy.config.control_step_budget,
                                       feedback=policy.config.task_feedback, hold_s=config.open_hold_s)
        write_json(recorder.path/'camera_profile.json', {
            'agent_cameras': {'agentview': 'robot0_agentview_left', 'wrist': 'robot0_eye_in_hand',
                              'navview': {'kind': 'render_only_free_camera', **NAVVIEW},
                              'vla_extra': 'robot0_agentview_right'},
            'agent_resolution': [256, 256], 'recorded_resolution': [config.width, config.height],
            'fovy_deg': {name: float(sim.model.cam_fovy[sim.model.camera(alias).id])
                         for name, alias in environment.cameras.items()}})
        agent = policy.run(environment, recorder.path/'agent', seed=seed)
        verdict = environment.verdict()
        stop = environment.stop
        if stop is not None:
            status, reason = stop['status'], stop['reason']
            if status == 'timeout' and verdict['status'] == 'success':
                status, reason = 'success', verdict['reason']
        elif agent.get('shared_failure'):
            status, reason, failure_scope = 'infrastructure_error', agent['shared_failure'], 'shared'
        elif agent['status'] == 'planner_error':
            # The model provider dropped the session: a shared service failure, not a task result.
            status, reason, failure_scope = 'infrastructure_error', 'agent_provider_error', 'shared'
        elif agent['status'] == 'timeout':
            status, reason = 'timeout', 'attempt_wall_clock_budget'
        elif agent['status'] in ('error', 'boot_error'):
            status, reason = 'episode_error', 'agent_' + agent['status']
        elif agent['status'] == 'call_budget':
            status, reason = 'failure', 'agent_call_budget_exhausted'
        else:
            status, reason = 'failure', 'agent_' + (agent.get('finish') or {}).get('status', 'ended') + '_without_success'
    except KeyboardInterrupt:
        status = 'incomplete'; reason = 'interrupted'
        raise
    except Exception as exc:
        classified = classify_episode_exception(exc, episode_phase())
        exception_detail = {**classified, 'traceback': traceback.format_exc()}
        status = classified['status']; reason = classified['reason']; failure_scope = classified['failure_scope']
        recorder.event('exception', message=str(exc), error_type=type(exc).__name__)
    finally:
        mark_episode_phase('finalize')
        physics.close()
        verdict = evidence.verdict()
        if environment is not None:
            environment.close()
        termination = {'status': 'completed' if status in ('success', 'failure') else status, 'reason': reason}
        task_outcome = {'status': status if status in ('success', 'failure') else 'unknown', 'reason': reason}
        outcome = {'status': status, 'reason': reason, 'verdict': verdict, 'protocol': PROTOCOL,
                   'agent_protocol': AGENT_PROTOCOL, 'execution_termination': termination,
                   'task_outcome': task_outcome, 'exception_detail': exception_detail,
                   'failure_scope': failure_scope or ('shared' if status == 'infrastructure_error' else None),
                   'operation_backend': 'pyrua', 'continuous_simulation': True,
                   'navigation': {'status': 'agent_controlled'}, 'seed': seed,
                   'elapsed_wall_s': time.monotonic()-started,
                   'control_steps': environment.steps if environment is not None else 0,
                   'agent': agent,
                   'trajectory_metrics': evidence.contact_stats.metrics(
                       task_success=status == 'success' if status in ('success', 'failure') else None,
                       simulation_valid=False if status == 'simulation_failure' else True if sim.started else None),
                   'contact_policy': CONTACT_POLICY,
                   'attribution': {'category': 'SimulationFailure' if status == 'simulation_failure' else
                                   'EpisodeTimeout' if status == 'timeout' else
                                   'PolicyFailure' if status == 'failure' else 'Unknown',
                                   'reason': reason, 'collisions': evidence.contact_stats.examples}}
        recorder.finish(status, reason)
    return {**outcome, 'status': recorder.result['status'], 'station_id': station['station_id'],
            'rollout_id': recorder.attempt_id, 'attempt': str(recorder.path), 'executed_steps': recorder.steps,
            'recording_complete': recorder.result['recording_complete'],
            'observation_complete': recorder.result['observation_complete']}
