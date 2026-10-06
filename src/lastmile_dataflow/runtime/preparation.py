"""Unbound construction preparation and bounded, real physical settling."""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import time

import mujoco
import numpy as np

from ..config import CollectionConfig
from ..io import write_json
from ..scenes.geometry import quat_angle
from ..scenes.graph import build_scene_graph
from ..scenes.initialization import initialize_robot
from .simulation import InitializationError, Simulation


@dataclass(frozen=True)
class SettleConfig:
    settle_s: float = 1.
    max_settle_s: float = 3.
    window_s: float = .25
    speed_m_s: float = .025
    angular_speed_rad_s: float = .10
    drift_m: float = .006
    rotation_rad: float = .04
    penetration_m: float = .01

    def __post_init__(self):
        for key, value in self.__dict__.items():
            if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
                raise ValueError('invalid settling setting: ' + key)
        if not self.window_s <= self.settle_s <= self.max_settle_s:
            raise ValueError('invalid settling window/budget')


def preparation_guard(sim):
    if sim.closed or sim.started or sim.robot.execution_started:
        raise RuntimeError('construction requires an open, unstarted simulation')


def settle_scene(sim, config=None, *, deadline=None):
    """Observe all non-robot moving bodies; never rewrite their pose during stepping.

    Static furniture is not required to have solver load-bearing contacts. Severe
    penetration is checked separately from stability, including the full scene.
    """
    preparation_guard(sim)
    config = config or SettleConfig()
    names = [sim.model.body(b).name for b in range(1, sim.model.nbody)
             if sim.model.body_dofnum[b] and not sim.model.body(b).name.startswith(sim.robot.config.namespace)]
    history = deque()
    initial_warnings = [int(w.number) for w in sim.data.warning]
    start = float(sim.data.time)
    steps = 0
    stable = False
    metrics = {}
    while sim.data.time - start < config.max_settle_s - 1e-10:
        preparation_guard(sim)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('construction wall-clock budget exhausted')
        mujoco.mj_step(sim.model, sim.data)
        mujoco.mj_forward(sim.model, sim.data)
        steps += 1
        if not np.isfinite(sim.state_vector()).all():
            raise ValueError('nonfinite simulation state during settling')
        state = {}
        for name in names:
            b = sim.model.body(name).id
            velocity = np.zeros(6)
            mujoco.mj_objectVelocity(sim.model, sim.data, mujoco.mjtObj.mjOBJ_BODY, b, velocity, 0)
            state[name] = (sim.data.xpos[b].copy(), sim.data.xquat[b].copy(), velocity)
        history.append((float(sim.data.time), state))
        while len(history) > 1 and history[1][0] <= sim.data.time - config.window_s:
            history.popleft()
        if sim.data.time - start < config.settle_s - 1e-10:
            continue
        stable = history[-1][0] - history[0][0] >= config.window_s - sim.model.opt.timestep - 1e-10
        metrics = {}
        for name in names:
            states = [s[name] for _, s in history]
            pos, quat, _ = states[0]
            metric = {'speed_m_s': max(float(np.linalg.norm(s[2][3:])) for s in states),
                      'angular_speed_rad_s': max(float(np.linalg.norm(s[2][:3])) for s in states),
                      'drift_m': max(float(np.linalg.norm(s[0]-pos)) for s in states),
                      'rotation_rad': max(quat_angle(s[1], quat) for s in states)}
            metrics[name] = metric
            stable &= all(metric[k] <= getattr(config, k) for k in metric)
        if stable:
            break
    penetration = [{'distance_m': float(c.dist),
                    'geoms': [sim.model.geom(int(c.geom1)).name, sim.model.geom(int(c.geom2)).name]}
                   for c in sim.data.contact if c.dist < -config.penetration_m
                   and sim.model.geom_bodyid[c.geom1] != sim.model.geom_bodyid[c.geom2]]
    warnings = [i for i, w in enumerate(sim.data.warning) if int(w.number) > initial_warnings[i]]
    return {'status': 'settled' if stable else 'unsettled', 'stable': bool(stable),
            'valid': bool(stable and not penetration and not warnings), 'steps': steps,
            'simulated_s': float(sim.data.time)-start, 'metrics': metrics,
            'severe_penetration': penetration, 'new_warnings': warnings}


class PreparedScene:
    def __init__(self, sim, initialization, settling):
        self.sim, self.initialization, self.settling = sim, initialization, settling
        self.graph = build_scene_graph(sim, stage='settled')
        self.station = self.graph.nodes['station_start']['pose']
        self.revision = 0

    def refresh(self):
        preparation_guard(self.sim)
        self.revision += 1
        self.graph = build_scene_graph(self.sim, revision=self.revision, station=self.station)
        return self.graph

    def save(self, path):
        preparation_guard(self.sim)
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        self.sim.freeze(path / 'scene')
        write_json(path / 'graph.json', self.graph.to_dict())
        write_json(path / 'preparation.json', {'initialization': self.initialization, 'settling': self.settling})

    def close(self):
        self.sim.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def prepare_scene(source, robot_config, collection=None, *, base=None, settle_config=None,
                  path=None, deadline=None, initial_snapshot=None):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('construction wall-clock budget exhausted')
    if initial_snapshot is not None:
        sim = Simulation.from_snapshot(initial_snapshot, robot_config, target=None)
        if sim.source.scene_id != source.scene_id or Path(sim.source.xml_path).resolve() != Path(source.xml_path).resolve():
            sim.close()
            raise ValueError('snapshot source does not match requested scene')
        sim.spec = None  # Source spec is materialized only for structural edits.
    else:
        sim = Simulation.from_source(source, robot_config, target=None)
    try:
        initialization = initialize_robot(sim, collection or CollectionConfig(), base=base)
        if initialization['status'] != 'valid':
            raise InitializationError('robot initialization failed: ' + initialization['reason'])
        settling = settle_scene(sim, settle_config, deadline=deadline)
        if not settling['valid']:
            error = InitializationError('baseline is unstable or physically invalid')
            error.details = {'stage': 'settling', 'settling': settling}
            raise error
        prepared = PreparedScene(sim, initialization, settling)
        if path is not None:
            prepared.save(path)
        return prepared
    except BaseException:
        sim.close()
        raise
