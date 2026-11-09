"""Spatial-block station split and physical Home initialization checks."""
from copy import deepcopy
import math

import mujoco
import numpy as np

from ...config import PandaOmronConfig
from ...runtime.simulation import Simulation
from .initialization import floor_support, room_triangles
from .sampling import disk_samples, robot_contacts


def spatial_split(points, block_m, fractions, seed):
    blocks = sorted({tuple(np.floor(np.asarray(p['xy']) / block_m).astype(int)) for p in points})
    order = np.random.default_rng(seed).permutation(len(blocks))
    n_train = int(len(blocks) * fractions[0])
    n_validation = int(len(blocks) * fractions[1])
    labels = ['train'] * n_train + ['validation'] * n_validation + ['acceptance'] * (len(blocks) - n_train - n_validation)
    assignment = {blocks[i]: label for i, label in zip(order, labels)}
    return [assignment[tuple(np.floor(np.asarray(p['xy']) / block_m).astype(int))] for p in points]


def prepare_stations(model, prepared, config):
    """CPU preparation only, before GPU episodes; no IK, FOV or reach filter."""
    from ..loading.rl_scene import RLSceneConfig
    scene = RLSceneConfig(**config['scene'])
    robot_config = PandaOmronConfig(model_path=scene.asset(scene.robot_xml),
                                    namespace='robot/robot_0/', physics_dt=scene.physics_dt,
                                    initial={'base_xyyaw': [0., 0., 0.], 'lift': scene.lift,
                                             'arm_q': {'right': list(scene.home)},
                                             'gripper_width': {'right': .08}})
    sim = Simulation(model, robot_config, prepared.catalog, prepared.target_body)
    parked = deepcopy(robot_config.initial)
    parked['base_xyyaw'] = [-10., -10., 0.]
    sim.robot.initialize(parked)
    for _ in range(round(config['environment']['settling_s'] / scene.physics_dt)):
        sim.robot.hold()
        mujoco.mj_step(model, sim.data)
    mujoco.mj_forward(model, sim.data)
    initial_qpos = sim.data.qpos.copy()
    # Each station uses the same settled objects, never the previous station's state.
    setting = config['stations']
    points = disk_samples(prepared.target_position, setting['radius_m'], setting['spacing_m'])
    splits = spatial_split(points, setting['block_m'], setting['split'], setting['seed'])
    triangles = room_triangles(sim)
    rows = []
    for point, split in zip(points, splits):
        xy = np.asarray(point['xy'])
        # The conservative circular footprint does not depend on yaw.
        footprint = [xy, *[xy + .4383 * np.array([math.cos(a), math.sin(a)])
                           for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]]
        supported = all(floor_support(sim, p, triangles=triangles) for p in footprint)
        for index in range(setting['yaw_bins']):
            yaw = -math.pi + index * 2 * math.pi / setting['yaw_bins']
            initial = deepcopy(robot_config.initial)
            initial['base_xyyaw'] = [*point['xy'], yaw]
            sim.data.qpos[:] = initial_qpos
            sim.robot.initialize(initial)
            collisions = robot_contacts(sim)
            reason = 'home_penetration' if collisions else '' if supported else 'floor_support'
            rows.append({'station_id': f"{point['xy_id']}-y{index:02d}",
                         'xy_id': point['xy_id'], 'x': float(xy[0]), 'y': float(xy[1]),
                         'yaw': yaw, 'split': split, 'valid': not reason, 'invalid_reason': reason})
    sim.close()
    return rows, initial_qpos
