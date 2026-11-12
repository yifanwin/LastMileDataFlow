"""Shared VLA rollout with embodiment observations and actual physical stepping."""
from collections.abc import Callable
from dataclasses import asdict, dataclass
import time

import imageio.v2 as imageio
import mujoco
import numpy as np

from ...data.recorder import AttemptRecorder
from ...data.rendering.analysis_video import AnalysisVideo
from ...data.rendering.cameras import ViewConfig
from ...data.rendering.head_views import head_frame
from ...policies.pi05.actions import execution_action as pi05_execution_action
from ...policies.pi05.observation import observation as pi05_observation
from ...policies.task import instruction
from ...utils.io import write_json
from ...utils.logging_config import trial_progress
from ..control.action import InvalidAction
from ..execution_config import TORSO_ERROR_POLICY
from ..outcomes.contacts import CONTACT_POLICY, ContactSampler, ContactStatistics, NavigationContacts, simulation_failure
from ..outcomes.task import VLA_PROTOCOL as PROTOCOL, PickMonitor, constraint, evaluate, torso_tracking_error
from .attempt import (attempt_result, failure_status, finish_navigation, open_attempt_sim,
                      trajectory_metrics, unexpected_failure)
from .lifecycle import isolated_episode, mark_episode_phase
from .navigation import navigate
from .protocol import OperationFailure

MOLMOBOT_RESOLUTION = (1024, 576)
MOLMOBOT_CAMERAS = ("wrist_camera_r", "head_camera", "wrist_camera_l")
VERDICT_EVERY_STEPS = 8
TERMINAL_VERDICTS = ("success", "infrastructure_error", "simulation_failure")
_ATTRIBUTION = {"simulation_failure": "SimulationFailure", "timeout": "EpisodeTimeout", "failure": "PolicyFailure"}


def molmobot_observation(sim, frames, prompt):
    observation = {name: frames[name] for name in MOLMOBOT_CAMERAS}
    observation.update(qpos={k: np.asarray(v, dtype=np.float32) for k, v in sim.robot.state().items()}, task=prompt)
    return observation


def unchanged_action(robot, action):
    return action


@dataclass(frozen=True)
class VLABackend:
    """What differs between VLA policies; the rollout itself is shared."""
    name: str
    schema_version: str
    control_policy: str
    resolution: tuple[int, int] | None   # None: use the execution config size
    camera_order: tuple[str, ...] | None  # None: the robot's own cameras
    fisheye: str | bool
    native_stepping: bool                 # MolmoBot 100 ms policy steps vs one control interval
    point_conditioning: bool              # open tasks get one first-frame target point
    observe: Callable
    to_execution: Callable


MOLMOBOT = VLABackend('molmobot', 'molmobot-station-v1', 'MolmoBot-RBY1Multitask', MOLMOBOT_RESOLUTION,
                      MOLMOBOT_CAMERAS, 'upstream image_warping_utils, once in model process', True, True,
                      molmobot_observation, unchanged_action)
PI05 = VLABackend('pi05', 'pi05-station-v1', 'pi05', None, None, False, False, False,
                  pi05_observation, pi05_execution_action)
BACKENDS = {backend.name: backend for backend in (MOLMOBOT, PI05)}


def first_point(sim, task):
    """One head-camera pixel on the target nearest the mask median, normalized for MolmoBot."""
    width, height = MOLMOBOT_RESOLUTION
    _, mask, _ = head_frame(sim, task["target_body"], ViewConfig(width=width, height=height))
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ValueError("open_target_not_visible")
    index = int(np.argmin((xs - np.median(xs)) ** 2 + (ys - np.median(ys)) ** 2))
    point = np.array([[xs[index] / width, ys[index] / height]], dtype=np.float32)
    return {task["instance_id"]: {"head_camera": {"points": point, "num_points": np.array([1])}}}, mask


class VLARollout:
    """One policy-driven attempt; also the NavigationRunner for its optional approach."""

    def __init__(self, baseline, task, station, config, collection, *, root, attempt_id, seed, policy,
                 path=None, goal=None):
        self.task, self.station, self.config, self.policy = task, station, config, policy
        self.seed, self.path, self.goal = seed, path, goal
        self.backend = BACKENDS[config.operation_backend]
        self.width, self.height = self.backend.resolution or (config.width, config.height)
        self.sim = open_attempt_sim(baseline, task, station, config)
        packet = {"schema_version": self.backend.schema_version, "robot": asdict(self.sim.robot.config),
                  "collection": asdict(collection), "task": task, "station": station, "protocol": asdict(config),
                  "contact_policy": CONTACT_POLICY, "seed": seed, "policy": asdict(policy.config),
                  "model_service": policy.metadata, "navigation_path": path, "navigation_goal": goal,
                  "scene": getattr(baseline, "scene_id", None)}
        self.recorder = AttemptRecorder(root, packet, attempt_id=attempt_id)
        self.recorder.bind_simulation(self.sim, width=self.width, height=self.height)
        robot = self.sim.robot
        self.all_fingers = {body for side in robot.arms for group in robot.finger_bodies(side) for body in group}
        self.monitors = {side: PickMonitor(self.sim, side, allowed_target_fingers=self.all_fingers)
                         for side in robot.arms}
        for monitor in self.monitors.values():
            monitor.initial.update(workspace_center_xy=task["anchor_world"][:2], workspace_radius_m=config.radius_m)
        self.target_bodies = next(iter(self.monitors.values())).target_bodies
        self.samples = {side: [] for side in self.monitors}
        self.contact_stats = ContactStatistics()
        self.contact_sampler = ContactSampler(self.sim)
        self.navigation_contacts = NavigationContacts()
        self.initial_height = float(self.sim.data.xpos[self.sim.target_id, 2])
        self.analysis = (AnalysisVideo(task, station, radius_m=config.radius_m)
                         if config.third_person_enabled and collection.video_cameras is None else None)
        self.status, self.reason = "infrastructure_error", "unfinished"
        self.verdict = self.exception_detail = None
        self.frames = self.points = self.initial_navigation_video = None
        self.failures = []
        self.started = time.monotonic()
        self.navigation = {"status": "not_requested"}
        self.phase = "vla_operation"

    # --- stepping -----------------------------------------------------------
    def _check_budget(self):
        if time.monotonic() - self.started >= self.config.attempt_timeout_s:
            raise TimeoutError("attempt_wall_clock_budget")

    def _ensure_started(self):
        if not self.sim.started:
            self.sim.begin()

    def _step(self, action, check, **kwargs):
        step = self.sim.step_policy if self.backend.native_stepping else self.sim.step
        return step(action, substep_check=check, **kwargs)

    def _capture_frames(self):
        self.frames = self.sim.render(self.width, self.height)
        self.recorder.video_frame(self._video(self.frames))

    def tick(self, command):
        """NavigationRunner.tick: one base command with navigation checks."""
        self._check_budget()
        self.recorder.phase = self.phase
        self._ensure_started()
        _, failure = self._step(command, self._navigation_check)
        if self.recorder.steps == 1:
            self.recorder.video_frame(self.initial_navigation_video)
        self._capture_frames()
        if failure:
            raise OperationFailure(failure)

    def _sample_contacts(self):
        self.contact_stats.update_frame(self.contact_sampler.capture(
            target_bodies=self.target_bodies, fingers=self.all_fingers))

    def _navigation_check(self):
        failure = simulation_failure(self.sim)
        if self.navigation_contacts.observe(self.sim, self.contact_sampler, self.config.navigation_max_penetration_m):
            failure = "navigation_severe_collision"
        self._sample_contacts()
        return failure

    def _operation_check(self):
        health = simulation_failure(self.sim)
        if health:
            self.failures.append(health)
            return health
        mujoco.mj_forward(self.sim.model, self.sim.data)
        self._sample_contacts()
        for side, monitor in self.monitors.items():
            facts = self._monitor_facts(monitor)
            self.samples[side].append(facts)
            violation = constraint(facts, monitor.initial)
            if violation:
                self.failures.append(violation)
        return min(self.failures, default=None)

    def _monitor_facts(self, monitor):
        robot = self.sim.robot
        monitor.phase = "vla_operation"
        monitor.command_h = robot.control_height()
        facts = monitor.sample()
        if "torso" in facts:
            facts["torso_tracking_error_rad"] = torso_tracking_error(facts)
        else:
            facts["lift_tracking_error_m"] = abs(facts["lift"] - robot.control_height())
        facts["torso_error_policy"] = TORSO_ERROR_POLICY
        if self.task["operation"] == "open":
            joint = self.sim.model.joint(self.task["joint_name"]).id
            facts["joint_value"] = float(self.sim.data.qpos[self.sim.model.jnt_qposadr[joint]])
        return facts

    def _video(self, frames):
        if not (self.analysis and "third_person_camera" in frames):
            return frames
        side = max(self.monitors, key=lambda s: len(self.samples[s][-1]["finger_forces_n"]) if self.samples[s] else 0)
        return {**frames, "third_person_camera": self.analysis.frame(
            frames, phase=self.phase, time_s=float(self.sim.data.time), base_xy=self.sim.robot.group("base")[:2],
            target_height=float(self.sim.data.xpos[self.sim.target_id, 2]), initial_height=self.initial_height,
            facts=self.samples[side][-1] if self.samples[side] else None, side=side,
            collisions=self.contact_stats.forbidden_contact_count,
            wrist_camera="robot0_eye_in_hand" if self.sim.robot.config.name == "panda_omron" else None)}

    # --- phases -------------------------------------------------------------
    def _navigate(self):
        self.navigation = {"status": "running"}
        self.phase = self.recorder.phase = "navigation"
        self.sim.begin()
        self.frames = self.sim.render(self.width, self.height)
        write_json(self.recorder.path / "initial_state.json", self.sim.observe_state())
        self.initial_navigation_video = self._video(self.frames)
        mark_episode_phase("execution")
        self.navigation = navigate(self, self.path, self.goal)
        write_json(self.recorder.path / "navigation.json", self.navigation)
        write_json(self.recorder.path / "operation_initial_state.json", self.sim.observe_state())
        self.phase = "vla_operation"

    def _prepare_policy(self):
        if self.task["operation"] == "open" and self.backend.point_conditioning:
            self.points, mask = first_point(self.sim, self.task)
            imageio.imwrite(self.recorder.path / "target_mask.png", mask.astype(np.uint8) * 255)
        self.recorder.phase = self.phase
        self._ensure_started()
        self.frames = self.sim.render(self.width, self.height)
        state_name = "operation_initial_state.json" if self.path is not None else "initial_state.json"
        write_json(self.recorder.path / state_name, self.sim.observe_state())
        cameras = self.sim.robot.camera_names
        write_json(self.recorder.path / "camera_profile.json", {
            "resolution": [self.width, self.height],
            "camera_order": list(self.backend.camera_order or cameras),
            "fovy_deg": {name: float(self.sim.model.cam_fovy[self.sim.model.camera(alias).id])
                         for name, alias in cameras.items()},
            "extrinsic_jitter": False, "fisheye": self.backend.fisheye})
        if self.sim.third_person_camera:
            write_json(self.recorder.path / "third_person_initial.json", self.sim.third_person_camera.metadata())
        if self.analysis is not None:
            write_json(self.recorder.path / "analysis_video.json", {
                "style": "actual observer + wrist inset + measured facts", "camera": "third_person_camera",
                "dimensions": [1280, 720], "planned_path": None, "policy_input": False,
                "robot_videos": f"raw {self.width}x{self.height} RGB"})
        mark_episode_phase("execution")

    def _request_action(self, step):
        prompt = instruction(self.task)
        observation = self.backend.observe(self.sim, self.frames, prompt)
        if step == 0 and self.points is not None:
            observation["object_image_points"] = self.points
        response = self.policy.action(observation, task=self.task, attempt=self.recorder.attempt_id,
                                      seed=self.seed, first=step == 0)
        self.recorder.phase = self.phase
        if step == 0:
            for i, pixels in enumerate(response.pop("processed_images") or []):
                imageio.imwrite(self.recorder.path / f"model_input_{i}.png", pixels)
            write_json(self.recorder.path / "policy_instruction.json", {
                "instruction": prompt, "data_layer": "derived", "source_frame_id": 0,
                "kind": "actual_policy_processed_input",
                "target_conditioning": "one first-frame point" if self.points else "language only",
                "model_prompt": response.get("model_prompt"),
                "original_conditioning_points": response.get("conditioning_points")})
        self._check_budget()
        return response

    def _apply_action(self, response, step):
        action = self.backend.to_execution(self.sim.robot, response["action"])
        initial_video = self._video(self.frames) if step == 0 else None
        self.failures = []
        self._ensure_started()
        _, failure = self._step(action, self._operation_check, command_raw=response["raw_action"])
        if step == 0 and self.path is None:
            self.recorder.video_frame(initial_video)
        self._capture_frames()
        return failure

    def _evaluate(self):
        return evaluate(self.task, self.samples, {side: m.initial for side, m in self.monitors.items()},
                        self.config.open_hold_s)

    def _run_policy(self):
        horizon = self.policy.config.task_horizon
        self.status, self.reason = "failure", "policy_step_budget_exhausted"
        for step in range(horizon):
            self._check_budget()
            failure = self._apply_action(self._request_action(step), step)
            trial_progress("VLA rollout 已执行策略步=%s/%s 耗时=%.1fs", step + 1, horizon,
                           time.monotonic() - self.started)
            if failure:
                self.reason = self.failures[0]
                self.status = failure_status(self.reason)
                return
            if step % VERDICT_EVERY_STEPS == VERDICT_EVERY_STEPS - 1 or step == horizon - 1:
                self.verdict = self._evaluate()
                if self.verdict["status"] in TERMINAL_VERDICTS:
                    self.status, self.reason = self.verdict["status"], self.verdict["reason"]
                    return

    def _finalize(self):
        if self.path is not None:
            self.navigation = finish_navigation(self.navigation, self.navigation_contacts, self.reason)
        mark_episode_phase("finalize")
        termination = {"status": "completed" if self.status in ("success", "failure") else self.status,
                       "reason": self.reason}
        if self.status == "timeout":
            self.verdict = self._evaluate()
            if self.verdict["status"] == "success":
                self.status, self.reason = "success", self.verdict["reason"]
        task_status = self.status if self.status in ("success", "failure") else "unknown"
        outcome = {
            "status": self.status, "reason": self.reason, "verdict": self.verdict, "protocol": PROTOCOL,
            "execution_termination": termination, "task_outcome": {"status": task_status, "reason": self.reason},
            "exception_detail": self.exception_detail, "operation_backend": self.config.operation_backend,
            "continuous_simulation": True, "navigation": self.navigation,
            "gripper_mode": getattr(self.policy.config, "gripper_mode", "embodiment_default"),
            "seed": self.seed, "elapsed_wall_s": time.monotonic() - self.started,
            "trajectory_metrics": trajectory_metrics(self.contact_stats, self.sim, status=self.status,
                                                     task_status=task_status),
            "contact_policy": CONTACT_POLICY,
            "attribution": {"category": _ATTRIBUTION.get(self.status, "Unknown"), "reason": self.reason,
                            "collisions": self.contact_stats.examples},
            "control": {"seed": self.seed, "policy": self.backend.control_policy}}
        self.recorder.finish(self.status, self.reason)
        return outcome

    def run(self):
        outcome = None
        try:
            if self.path is not None:
                self._navigate()
            self._prepare_policy()
            self._run_policy()
        except OperationFailure as exc:
            self.reason = exc.reason
            self.status = failure_status(self.reason)
            if self.phase in ("navigation", "arrival"):
                self.navigation = {"status": "failure", "reason": self.reason}
        except InvalidAction as exc:
            self.status, self.reason = "failure", "invalid_policy_action:" + str(exc)
            self.recorder.event("invalid_policy_action", message=str(exc))
        except TimeoutError as exc:
            self.status, self.reason = "timeout", str(exc)
            self.recorder.event("episode_timeout", message=self.reason)
        except KeyboardInterrupt:
            self.status, self.reason = "incomplete", "interrupted"
            raise
        except Exception as exc:
            self.status, self.reason, self.exception_detail = unexpected_failure(exc, self.recorder, self.phase)
        finally:
            outcome = self._finalize()
        return attempt_result(outcome, self.recorder, self.station)


@isolated_episode
def run_vla_attempt(baseline, task, station, config, collection, *, root, attempt_id, seed, policy,
                    path=None, goal=None):
    return VLARollout(baseline, task, station, config, collection, root=root, attempt_id=attempt_id, seed=seed,
                      policy=policy, path=path, goal=goal).run()
