"""Shared VLA rollout with embodiment observations and actual physical stepping."""

from dataclasses import asdict, dataclass, field
from collections.abc import Callable
from ..simulation import Simulation
from ..execution_config import ExecutionConfig
import time
import traceback
import imageio.v2 as imageio
import mujoco
import numpy as np
from .lifecycle import (
    isolated_episode,
    mark_episode_phase,
    classify_episode_exception,
    episode_phase,
)
from ...utils.logging_config import trial_progress
from ...utils.io import write_json
from ...data.recorder import AttemptRecorder
from ...data.rendering.head_views import head_frame
from ...data.rendering.edit_views import ViewConfig
from ...data.rendering.analysis_video import AnalysisVideo
from ...data.rendering.third_person import ThirdPersonCamera
from ...policies.task import instruction
from ...scenes.stations.sampling import place_frozen_station
from ..outcomes.contacts import (
    ContactSampler,
    ContactStatistics,
    NavigationContacts,
    simulation_failure,
    SIMULATION_FAILURES,
    CONTACT_POLICY,
)
from ..outcomes.pick import PickMonitor
from ..outcomes.evaluation import torso_tracking_error
from ..outcomes.evaluation import constraint, evaluate, VLA_PROTOCOL as PROTOCOL
from ..control.action import InvalidAction
from .context import clone_sim, OperationFailure
from .navigation import navigate


def first_point(sim, task):
    (_, mask, visibility) = head_frame(
        sim, task["target_body"], ViewConfig(width=1024, height=576)
    )
    (ys, xs) = np.nonzero(mask)
    if not len(xs):
        raise ValueError("open_target_not_visible")
    distances = (xs - np.median(xs)) ** 2 + (ys - np.median(ys)) ** 2
    index = int(np.argmin(distances))
    point = np.array([[xs[index] / 1024.0, ys[index] / 576.0]], dtype=np.float32)
    return (
        {
            task["instance_id"]: {
                "head_camera": {"points": point, "num_points": np.array([1])}
            }
        },
        mask,
    )


@dataclass
class _PolicyNavigation:
    sim: Simulation
    config: ExecutionConfig
    tick: Callable
    navigation_contacts: NavigationContacts
    phase: str = "navigation"
    monitor: PickMonitor | None = None
    fingers: set[int] = field(default_factory=set)

    @staticmethod
    def yaw_gap(a, b):
        return float((a - b + np.pi) % (2 * np.pi) - np.pi)

    def hold_torso_target(self):
        return self.sim.robot.control_height()


class _VLARollout:
    def __init__(
        self,
        baseline,
        task,
        station,
        config,
        collection,
        *,
        root,
        attempt_id,
        seed,
        policy,
        path=None,
        goal=None,
    ):
        self.attempt_id = attempt_id
        self.baseline = baseline
        self.collection = collection
        self.config = config
        self.goal = goal
        self.path = path
        self.policy = policy
        self.root = root
        self.seed = seed
        self.station = station
        self.task = task
        self.sim = clone_sim(
            self.baseline,
            target=self.task["target_body"],
            handle=self.task.get("handle_body"),
        )
        place_frozen_station(self.sim, self.station)
        self.pi05 = self.config.operation_backend == "pi05"
        (self.width, self.height) = (
            (self.config.width, self.config.height) if self.pi05 else (1024, 576)
        )
        if self.config.third_person_enabled:
            self.sim.third_person_camera = ThirdPersonCamera()
        packet = {
            "schema_version": "pi05-station-v1" if self.pi05 else "molmobot-station-v1",
            "robot": asdict(self.sim.robot.config),
            "collection": asdict(self.collection),
            "task": self.task,
            "station": self.station,
            "protocol": asdict(self.config),
            "contact_policy": CONTACT_POLICY,
            "seed": self.seed,
            "policy": asdict(self.policy.config),
            "model_service": self.policy.metadata,
            "navigation_path": self.path,
            "navigation_goal": self.goal,
            "scene": getattr(self.baseline, "scene_id", None),
        }
        self.recorder = AttemptRecorder(self.root, packet, attempt_id=self.attempt_id)
        self.recorder.bind_simulation(self.sim, width=self.width, height=self.height)
        self.all_fingers = {
            body
            for side in self.sim.robot.arms
            for group in self.sim.robot.finger_bodies(side)
            for body in group
        }
        self.monitors = {
            side: PickMonitor(self.sim, side, allowed_target_fingers=self.all_fingers)
            for side in self.sim.robot.arms
        }
        self.primary_monitor = next(iter(self.monitors.values()))
        for monitor in self.monitors.values():
            monitor.initial.update(
                workspace_center_xy=self.task["anchor_world"][:2],
                workspace_radius_m=self.config.radius_m,
            )
        self.samples = {side: [] for side in self.monitors}
        self.contact_stats = ContactStatistics()
        self.collisions = self.contact_stats.examples
        self.contact_sampler = ContactSampler(self.sim)
        self.navigation_contacts = NavigationContacts()
        self.initial_height = float(self.sim.data.xpos[self.sim.target_id, 2])
        self.frames = None
        self.analysis = (
            AnalysisVideo(self.task, self.station, radius_m=self.config.radius_m)
            if self.config.third_person_enabled
            and self.collection.video_cameras is None
            else None
        )
        self.status = "infrastructure_error"
        self.reason = "unfinished"
        self.verdict = None
        self.exception_detail = None
        self.started = time.monotonic()
        self.budget = self.config.attempt_timeout_s
        self.navigation = {"status": "not_requested"}
        self.phase = "vla_operation"

    def _policy_step(self, action, **kwargs):
        return (self.sim.step if self.pi05 else self.sim.step_policy)(action, **kwargs)

    def _state(self):
        return self.sim.observe_state()

    def _video(self, frames):
        result = dict(frames)
        if self.analysis and "third_person_camera" in frames:
            side = max(
                self.monitors,
                key=lambda s: (
                    len(self.samples[s][-1]["finger_forces_n"])
                    if self.samples[s]
                    else 0
                ),
            )
            result["third_person_camera"] = self.analysis.frame(
                frames,
                phase=self.phase,
                time_s=float(self.sim.data.time),
                base_xy=self.sim.robot.group("base")[:2],
                target_height=float(self.sim.data.xpos[self.sim.target_id, 2]),
                initial_height=self.initial_height,
                facts=self.samples[side][-1] if self.samples[side] else None,
                side=side,
                collisions=self.contact_stats.forbidden_contact_count,
                wrist_camera="robot0_eye_in_hand"
                if self.sim.robot.config.name == "panda_omron"
                else None,
            )
        return result

    def tick(self, action):
        if time.monotonic() - self.started >= self.budget:
            raise TimeoutError("attempt_wall_clock_budget")
        self.recorder.phase = self.navigation_context.phase
        if not self.sim.started:
            self.sim.begin()
        (command, stop) = self._policy_step(
            action, substep_check=self._navigation_check
        )
        if self.recorder.steps == 1:
            self.recorder.video_frame(self.initial_navigation_video)
        self.frames = self.sim.render(self.width, self.height)
        self.recorder.video_frame(self._video(self.frames))
        if stop:
            raise OperationFailure(stop)

    def _operation_check(self):
        health = simulation_failure(self.sim)
        if health:
            self.failures.append(health)
            return health
        mujoco.mj_forward(self.sim.model, self.sim.data)
        self._sample_contacts()
        for side, monitor in self.monitors.items():
            monitor.phase = "vla_operation"
            monitor.command_h = self.sim.robot.control_height()
            facts = monitor.sample()
            if "torso" in facts:
                facts["torso_tracking_error_rad"] = torso_tracking_error(facts)
            else:
                facts["lift_tracking_error_m"] = abs(
                    facts["lift"] - self.sim.robot.control_height()
                )
            facts["torso_error_policy"] = "record_only"
            if self.task["operation"] == "open":
                joint = self.sim.model.joint(self.task["joint_name"]).id
                facts["joint_value"] = float(
                    self.sim.data.qpos[self.sim.model.jnt_qposadr[joint]]
                )
            self.samples[side].append(facts)
            violation = constraint(facts, monitor.initial)
            if violation:
                self.failures.append(violation)
        return min(self.failures, default=None)

    def _navigation_check(self):
        health = simulation_failure(self.sim)
        if self.navigation_context.navigation_contacts.observe(
            self.sim, self.contact_sampler, self.config.navigation_max_penetration_m
        ):
            health = "navigation_severe_collision"
        self._sample_contacts()
        return health

    def _sample_contacts(self):
        self.contact_stats.update_frame(self.contact_sampler.capture(
            target_bodies=self.primary_monitor.target_bodies, fingers=self.all_fingers))

    def _navigate(self):
        if self.goal is None:
            raise ValueError("navigation requires goal")
        self.phase = "navigation"
        self.navigation = {"status": "running"}
        self.recorder.phase = self.phase
        self.sim.begin()
        self.frames = self.sim.render(self.width, self.height)
        write_json(self.recorder.path / "initial_state.json", self._state())
        self.initial_navigation_video = self._video(self.frames)
        self.navigation_context = _PolicyNavigation(
            self.sim, self.config, self.tick, self.navigation_contacts
        )
        mark_episode_phase("execution")
        self.navigation = navigate(self.navigation_context, self.path, self.goal)
        delta = np.asarray(self.goal["base"]) - self.sim.robot.group("base")
        delta[2] = self.navigation_context.yaw_gap(
            self.goal["base"][2], self.sim.robot.group("base")[2]
        )
        if (
            np.linalg.norm(delta[:2]) > self.config.arrival_tolerance_m
            or abs(delta[2]) > self.config.arrival_tolerance_rad
        ):
            raise OperationFailure("arrival_tracking_error")
        write_json(self.recorder.path / "navigation.json", self.navigation)
        write_json(self.recorder.path / "operation_initial_state.json", self._state())
        self.phase = "vla_operation"

    def _prepare_policy(self):
        self.points = None
        if self.task["operation"] == "open" and (not self.pi05):
            (self.points, mask) = first_point(self.sim, self.task)
            imageio.imwrite(
                self.recorder.path / "target_mask.png", mask.astype(np.uint8) * 255
            )
        self.recorder.phase = self.phase
        if not self.sim.started:
            self.sim.begin()
        self.frames = self.sim.render(self.width, self.height)
        write_json(
            self.recorder.path
            / (
                "operation_initial_state.json"
                if self.path is not None
                else "initial_state.json"
            ),
            self._state(),
        )
        write_json(
            self.recorder.path / "camera_profile.json",
            {
                "resolution": [self.width, self.height],
                "camera_order": list(self.sim.robot.camera_names)
                if self.pi05
                else ["wrist_camera_r", "head_camera", "wrist_camera_l"],
                "fovy_deg": {
                    name: float(
                        self.sim.model.cam_fovy[self.sim.model.camera(alias).id]
                    )
                    for (name, alias) in self.sim.robot.camera_names.items()
                },
                "extrinsic_jitter": False,
                "fisheye": False
                if self.pi05
                else "upstream image_warping_utils, once in model process",
            },
        )
        if self.sim.third_person_camera:
            write_json(
                self.recorder.path / "third_person_initial.json",
                self.sim.third_person_camera.metadata(),
            )
        if self.analysis is not None:
            write_json(
                self.recorder.path / "analysis_video.json",
                {
                    "style": "actual observer + wrist inset + measured facts",
                    "camera": "third_person_camera",
                    "dimensions": [1280, 720],
                    "planned_path": None,
                    "policy_input": False,
                    "robot_videos": f"raw {self.width}x{self.height} RGB",
                },
            )
        mark_episode_phase("execution")

    def _request_action(self, step):
        if self.pi05:
            from ...policies.pi05.observation import observation as pi05_observation

            observation = pi05_observation(
                self.sim, self.frames, instruction(self.task)
            )
        else:
            observation = {
                k: self.frames[k]
                for k in ("wrist_camera_r", "head_camera", "wrist_camera_l")
            }
            observation.update(
                qpos={
                    k: np.asarray(v, dtype=np.float32)
                    for (k, v) in self.sim.robot.state().items()
                },
                task=instruction(self.task),
            )
        if step == 0 and self.points is not None:
            observation["object_image_points"] = self.points
        response = self.policy.action(
            observation,
            task=self.task,
            attempt=self.attempt_id,
            seed=self.seed,
            first=step == 0,
        )
        self.recorder.phase = self.phase
        if step == 0:
            for i, pixels in enumerate(response.pop("processed_images") or []):
                imageio.imwrite(self.recorder.path / f"model_input_{i}.png", pixels)
            write_json(
                self.recorder.path / "policy_instruction.json",
                {
                    "instruction": instruction(self.task),
                    "data_layer": "derived",
                    "source_frame_id": 0,
                    "kind": "actual_policy_processed_input",
                    "target_conditioning": "one first-frame point"
                    if self.points
                    else "language only",
                    "model_prompt": response.get("model_prompt"),
                    "original_conditioning_points": response.get("conditioning_points"),
                },
            )
        if time.monotonic() - self.started >= self.budget:
            raise TimeoutError("attempt_wall_clock_budget")
        return response

    def _apply_action(self, response, step):
        action = response["action"]
        if self.pi05:
            from ...policies.pi05.actions import execution_action

            action = execution_action(self.sim.robot, action)
        initial_video = self._video(self.frames) if step == 0 else None
        self.failures = []
        if not self.sim.started:
            self.sim.begin()
        (command, stop) = self._policy_step(
            action,
            substep_check=self._operation_check,
            command_raw=response["raw_action"],
        )
        if step == 0 and self.path is None:
            self.recorder.video_frame(initial_video)
        self.frames = self.sim.render(self.width, self.height)
        self.recorder.video_frame(self._video(self.frames))
        return stop

    def _run_policy(self):
        for step in range(self.policy.config.task_horizon):
            remaining = self.budget - (time.monotonic() - self.started)
            if remaining <= 0:
                raise TimeoutError("attempt_wall_clock_budget")
            response = self._request_action(step)
            stop = self._apply_action(response, step)
            trial_progress(
                "VLA rollout 已执行策略步=%s/%s 耗时=%.1fs",
                step + 1,
                self.policy.config.task_horizon,
                time.monotonic() - self.started,
            )
            if stop:
                self.reason = self.failures[0]
                self.status = (
                    "simulation_failure"
                    if self.reason in SIMULATION_FAILURES
                    else "failure"
                )
                break
            if step % 8 == 7 or step == self.policy.config.task_horizon - 1:
                self.verdict = evaluate(
                    self.task,
                    self.samples,
                    {s: m.initial for (s, m) in self.monitors.items()},
                    self.config.open_hold_s,
                )
                if self.verdict["status"] in (
                    "success",
                    "infrastructure_error",
                    "simulation_failure",
                ):
                    self.status = self.verdict["status"]
                    self.reason = self.verdict["reason"]
                    break
        else:
            self.status = "failure"
            self.reason = "policy_step_budget_exhausted"
        if self.status == "infrastructure_error" and self.reason == "unfinished":
            self.status = "failure"
            self.reason = "policy_step_budget_exhausted"

    def _finalize(self):
        if self.path is not None:
            self.navigation.update(asdict(self.navigation_contacts))
        if self.navigation["status"] == "running":
            self.navigation.update(status="unknown", reason=self.reason)
        mark_episode_phase("finalize")
        termination = {
            "status": "completed"
            if self.status in ("success", "failure")
            else self.status,
            "reason": self.reason,
        }
        if self.status == "timeout":
            self.verdict = evaluate(
                self.task,
                self.samples,
                {side: monitor.initial for (side, monitor) in self.monitors.items()},
                self.config.open_hold_s,
            )
            if self.verdict["status"] == "success":
                (self.status, self.reason) = ("success", self.verdict["reason"])
        task_outcome = {
            "status": self.status
            if self.status in ("success", "failure")
            else "unknown",
            "reason": self.reason,
        }
        self.outcome = {
            "status": self.status,
            "reason": self.reason,
            "verdict": self.verdict,
            "protocol": PROTOCOL,
            "execution_termination": termination,
            "task_outcome": task_outcome,
            "exception_detail": self.exception_detail,
            "operation_backend": self.config.operation_backend,
            "continuous_simulation": True,
            "navigation": self.navigation,
            "gripper_mode": getattr(
                self.policy.config, "gripper_mode", "embodiment_default"
            ),
            "seed": self.seed,
            "elapsed_wall_s": time.monotonic() - self.started,
            "trajectory_metrics": self.contact_stats.metrics(
                task_success=self.status == "success"
                if self.status in ("success", "failure")
                else None,
                simulation_valid=False
                if self.status == "simulation_failure"
                else True
                if self.sim.started
                else None,
            ),
            "contact_policy": CONTACT_POLICY,
            "attribution": {
                "category": "SimulationFailure"
                if self.status == "simulation_failure"
                else "EpisodeTimeout"
                if self.status == "timeout"
                else "PolicyFailure"
                if self.status == "failure"
                else "Unknown",
                "reason": self.reason,
                "collisions": self.collisions,
            },
            "control": {
                "seed": self.seed,
                "policy": "pi05" if self.pi05 else "MolmoBot-RBY1Multitask",
            },
        }
        self.recorder.finish(self.status, self.reason)

    def run(self):
        try:
            if self.path is not None:
                self._navigate()
            self._prepare_policy()
            self._run_policy()
        except OperationFailure as exc:
            self.reason = exc.reason
            self.status = (
                "simulation_failure"
                if self.reason in SIMULATION_FAILURES
                else "failure"
            )
            if (
                self.navigation["status"] in ("running", "success")
                and self.phase == "navigation"
            ):
                self.navigation = {"status": "failure", "reason": self.reason}
        except InvalidAction as exc:
            self.status = "failure"
            self.reason = "invalid_policy_action:" + str(exc)
            self.recorder.event("invalid_policy_action", message=str(exc))
        except TimeoutError as exc:
            self.status = "timeout"
            self.reason = str(exc)
            self.recorder.event("episode_timeout", message=self.reason)
        except KeyboardInterrupt:
            self.status = "incomplete"
            self.reason = "interrupted"
            raise
        except Exception as exc:
            classified = classify_episode_exception(exc, episode_phase())
            self.exception_detail = {**classified, "traceback": traceback.format_exc()}
            self.status = classified["status"]
            self.reason = classified["reason"]
            self.recorder.event(
                "exception", message=str(exc), error_type=type(exc).__name__
            )
        finally:
            self._finalize()
        return {
            **self.outcome,
            "status": self.recorder.result["status"],
            "station_id": self.station["station_id"],
            "rollout_id": self.recorder.attempt_id,
            "attempt": str(self.recorder.path),
            "executed_steps": self.recorder.steps,
            "recording_complete": self.recorder.result["recording_complete"],
            "observation_complete": self.recorder.result["observation_complete"],
        }


@isolated_episode
def run_vla_attempt(
    baseline,
    task,
    station,
    config,
    collection,
    *,
    root,
    attempt_id,
    seed,
    policy,
    path=None,
    goal=None,
):
    return _VLARollout(
        baseline,
        task,
        station,
        config,
        collection,
        root=root,
        attempt_id=attempt_id,
        seed=seed,
        policy=policy,
        path=path,
        goal=goal,
    ).run()
