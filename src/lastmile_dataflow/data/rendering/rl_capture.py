"""Policy-step trajectory selection shared by periodic validation videos."""
from pathlib import Path
import json

import numpy as np

from .rl_video import render_rollout


class RolloutCapture:
    def __init__(self, output, transitions, config, representatives=False):
        self.output = Path(output)
        self.transitions = transitions
        self.config = config
        self.representatives = representatives
        self.videos = []

    def initialize(self, env, stations, seed, deterministic):
        self.stations, self.seed = stations, seed
        self.mode = 'mean' if deterministic else 'stochastic'
        self.states = [[] for _ in stations]
        self.metrics = [[] for _ in stations]

    def sample(self, env, active):
        task = env.task
        dp, dr = task.goal_error()
        distances, angles = dp.norm(dim=-1).cpu().tolist(), dr.norm(dim=-1).cpu().tolist()
        positions = env.sim.data.qpos.cpu().numpy()
        for i in active.nonzero(as_tuple=True)[0].tolist():
            self.states[i].append(positions[i].copy())
            both = bool((task.forces[i] > 1e-6).all())
            stage = ('LIFT' if both else 'Grasp' if distances[i] < .01 and angles[i] < np.deg2rad(10)
                     else 'Pre-grasp' if distances[i] < .04 else 'Approach')
            width = float(task.robot.data.joint_pos[i, task.fingers[0]] - task.robot.data.joint_pos[i, task.fingers[1]])
            lift = float(env.sim.data.xpos[i, task.target, 2] - task.height_reference[i])
            self.metrics[i].append(f'{stage} t={float(task.elapsed[i]):.2f} d={distances[i]:.3f} angle={angles[i]:.3f} '
                                   f'width={width:.3f} both={both} lift={lift:.3f} peak={float(task.lift_peak[i])*.01:.3f} '
                                   f'hold={float(task.hold[i]):.3f} collision={bool(task.step_collision[i])}')

    def finish(self, env, records):
        selected = set()
        for i, row in enumerate(records):
            station = env.task.stations[self.stations[i]]
            if self.representatives:
                category = 'success' if row['success'] else 'collision' if not row['collision_free'] else None
                if category is None or (self.output / 'replays' / f'first-{category}.json').exists():
                    continue
            else:
                category = station['split']
                if category in selected:
                    continue
            selected.add(category)
            stem = f'actor-{self.transitions:012d}-{station["split"]}-{station["station_id"]}-{self.mode}'
            replay_dir = self.output / 'replays'
            replay_dir.mkdir(exist_ok=True)
            replay = replay_dir / (stem + '.npz')
            if replay.exists():
                continue
            states = np.asarray(self.states[i])
            np.savez_compressed(replay, qpos=states, metrics=np.asarray(self.metrics[i]), dt=env.step_dt)
            result = 'success' if row['success'] else 'failure' if row['infrastructure_failure'] else 'terminated/timeout'
            result += f' collision_free={row["collision_free"]}'
            label = f'{self.transitions} {station["split"]} {station["station_id"]} seed={self.seed} {self.mode}'
            settings = {**self.config['video'], 'lookat': env.prepared.target_position.tolist()}
            video = render_rollout(env.sim.mj_model, states, self.metrics[i], self.output / 'views' / (stem + '.mp4'),
                                   label, result, settings, env.step_dt)
            self.videos.append({**video, 'replay': str(replay), 'seed': self.seed, 'result': row,
                                'transitions': self.transitions, 'policy': self.mode})
            if self.representatives:
                (replay_dir / f'first-{category}.json').write_text(json.dumps(self.videos[-1], indent=2))
