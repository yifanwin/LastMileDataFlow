"""Policy-step trajectory selection shared by periodic validation videos."""

from pathlib import Path

import numpy as np
import torch

from .video import render_rollout


class RolloutCapture:
    def __init__(self, output, transitions, config, representatives=False, *, selected_slots=None, trial_ids=None, trial_seeds=None):
        self.output = Path(output)
        self.transitions = transitions
        self.config = config
        self.representatives = representatives
        self.selected_slots = selected_slots
        self.videos = []
        self.trial_ids = trial_ids
        self.trial_seeds = trial_seeds

    def initialize(self, env, stations, seed, deterministic):
        self.stations, self.seed = stations, seed
        self.mode = "mean" if deterministic else "stochastic"
        if self.selected_slots is None:
            self.selected_slots = list(range(len(stations)))
        self.frames, self.facts, self.active = [], [], []
        self.sample(env, torch.ones(env.num_envs, dtype=torch.bool, device=env.device))

    def sample(self, env, active):
        task = env.task
        ids = self.selected_slots
        dp, dr = task.goal_error()
        q = task.robot.data.joint_pos
        facts = torch.stack((
            task.elapsed, dp.norm(dim=-1), dr.norm(dim=-1),
            q[:, task.fingers[0]] - q[:, task.fingers[1]],
            (task.forces > 1e-6).all(-1).float(),
            env.sim.data.xpos[:, task.target, 2] - task.height_reference,
            task.lift_peak * 0.01, task.hold, task.step_collision.float(),
        ), dim=-1)
        # Copy only selected video lanes; defer CPU transfers and formatting until the episode ends.
        self.frames.append(env.sim.data.qpos[ids].clone())
        self.facts.append(facts[ids].clone())
        self.active.append(active[ids].clone())

    def prepare_frames(self):
        frames = torch.stack(self.frames).cpu().numpy()
        facts = torch.stack(self.facts).cpu().numpy()
        active = torch.stack(self.active).cpu().numpy()
        self.states = [[] for _ in self.stations]
        self.metrics = [[] for _ in self.stations]
        for lane, slot in enumerate(self.selected_slots):
            self.states[slot] = frames[active[:, lane], lane]
            for t, distance, angle, width, both, lift, peak, hold, collision in facts[active[:, lane], lane]:
                stage = "LIFT" if both else "Grasp" if distance < 0.01 and angle < np.deg2rad(10) else "Pre-grasp" if distance < 0.04 else "Approach"
                self.metrics[slot].append(
                    f"{stage} t={t:.2f} d={distance:.3f} angle={angle:.3f} width={width:.3f} "
                    f"both={bool(both)} lift={lift:.3f} peak={peak:.3f} hold={hold:.3f} collision={bool(collision)}"
                )

    def finish(self, env, records):
        if hasattr(self, "frames"):
            self.prepare_frames()
        selected = set()
        for i in self.selected_slots if self.selected_slots is not None else range(len(records)):
            row = records[i]
            station = env.task.stations[self.stations[i]]
            if self.representatives:
                categories = {
                    name
                    for name, match in (("success", row["success"]), ("collision", not row["collision_free"]))
                    if match
                }
                if not hasattr(env, "_representative_video_categories"):
                    env._representative_video_categories = set()
                categories -= env._representative_video_categories
                if not categories:
                    continue
            else:
                categories = {station["split"]}
                if self.trial_ids is None and categories <= selected:
                    continue
            selected.update(categories)
            stem = f"actor-{self.transitions:012d}-{station['split']}-{station['station_id']}-{self.mode}"
            if self.trial_ids is not None:
                stem += f"-t{self.trial_ids[i]:03d}"
            seed = self.seed if self.trial_seeds is None else self.trial_seeds[i]
            replay_dir = self.output / "replays"
            replay_dir.mkdir(exist_ok=True)
            replay = replay_dir / (stem + ".npz")
            if replay.exists():
                continue
            states = np.asarray(self.states[i])
            np.savez_compressed(replay, qpos=states, metrics=np.asarray(self.metrics[i]), dt=env.step_dt)
            result = (
                "success"
                if row["success"]
                else "failure"
                if row["infrastructure_failure"] or row["failed"]
                else "timeout"
            )
            result += f" collision_free={row['collision_free']}"
            label = f"{self.transitions} {station['split']} {station['station_id']} seed={seed} {self.mode}"
            settings = {**self.config["video"], "target_body": env.prepared.target_body}
            print(f"Rendering {stem} seed={seed}", flush=True)
            video = render_rollout(
                env.sim.mj_model,
                states,
                self.metrics[i],
                self.output / "views" / (stem + ".mp4"),
                label,
                result,
                settings,
                env.step_dt,
            )
            self.videos.append(
                {
                    **video,
                    "replay": str(replay),
                    "seed": seed,
                    **({"trial": self.trial_ids[i]} if self.trial_ids is not None else {}),
                    "result": row,
                    "transitions": self.transitions,
                    "policy": self.mode,
                    "categories": sorted(categories),
                }
            )
            if self.representatives:
                env._representative_video_categories.update(categories)


class EpisodeReplays:
    """Full policy-step graph replays; transfer one completed trial at a time."""

    def initialize(self, env):
        self.qpos = [env.sim.data.qpos.clone()]
        self.qvel = [env.sim.data.qvel.clone()]
        self.actions, self.controls = [], []

    def sample(self, env, action):
        self.qpos.append(env.sim.data.qpos.clone())
        self.qvel.append(env.sim.data.qvel.clone())
        self.actions.append(action.clone())
        self.controls.append(env.sim.data.ctrl.clone())

    def save(self, index, steps, path):
        np.savez_compressed(
            path,
            qpos=torch.stack([q[index] for q in self.qpos[:steps + 1]]).cpu().numpy(),
            qvel=torch.stack([q[index] for q in self.qvel[:steps + 1]]).cpu().numpy(),
            action=torch.stack([a[index] for a in self.actions[:steps]]).cpu().numpy(),
            ctrl=torch.stack([c[index] for c in self.controls[:steps]]).cpu().numpy(),
        )
