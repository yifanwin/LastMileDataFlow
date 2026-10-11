#!/usr/bin/env python3
"""Verify the frozen RBY1M reach/quarter-chassis sampling preset; no physics stepping."""
import argparse
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.io import file_digest, write_json
from lastmile_dataflow.robots.rby1 import prepare_robot_spec
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.mjcf import load_spec
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.stations.no_edit_sampling import disk_samples
from lastmile_dataflow.tasks.raw_scene import robot_footprint


# Joint-limit-respecting FK witnesses. Chain length is an upper bound; attaining
# it proves maximum shoulder-to-TCP distance for this model, without IK percentiles.
WITNESSES = {
    'left': [1.04177612, 1.12884609, -1.67331159, -0.2323568,
             -3.14164028, -0.12050672, -1.09608192],
    'right': [-0.8715194, -1.43323787, -0.34558223, -0.23235493,
              -3.14160682, -0.12050428, 0.67751775],
}


def measure(model_path):
    robot = RobotConfig(str(Path(model_path).resolve()))
    spec = load_spec(robot.model_path)
    prepare_robot_spec(spec, robot)
    sim = Simulation(spec.compile(), robot, [])
    try:
        sim.robot.initialize()
        footprint = robot_footprint(sim)
        arms = {}
        for side, q in WITNESSES.items():
            model = sim.model
            shoulder = model.body('robot_0/link_' + side + '_arm_0').id
            site = model.site('robot_0/ee_site_' + side[0]).id
            body = int(model.site_bodyid[site])
            length = float(np.linalg.norm(model.site_pos[site]))
            segments = []
            while body != shoulder:
                if body == 0:
                    raise ValueError('TCP is not descended from shoulder')
                joints = range(int(model.body_jntadr[body]),
                               int(model.body_jntadr[body] + model.body_jntnum[body]))
                if any(np.linalg.norm(model.jnt_pos[j]) > 1e-12 for j in joints):
                    raise ValueError('Non-origin joint anchors need a different reach bound')
                segment = float(np.linalg.norm(model.body_pos[body]))
                segments.append({'body': model.body(body).name, 'length_m': segment})
                length += segment
                body = int(model.body_parentid[body])
            ids = [model.joint(f'robot_0/{side}_arm_{i}').id for i in range(7)]
            bounds = model.jnt_range[ids]
            if not np.all((np.asarray(q) >= bounds[:, 0]) & (np.asarray(q) <= bounds[:, 1])):
                raise ValueError('FK witness exceeds joint limits')
            sim.data.qpos[model.jnt_qposadr[ids]] = q
            mujoco.mj_kinematics(model, sim.data)
            achieved = float(np.linalg.norm(sim.data.site_xpos[site] - sim.data.xpos[shoulder]))
            if abs(achieved - length) > 1e-8:
                raise ValueError('Chain upper bound is not attained by the FK witness')
            arms[side] = {'maximum_reach_m': length, 'witness_reach_m': achieved,
                          'witness_q_rad': q, 'segments': segments,
                          'tcp_local_offset_m': float(np.linalg.norm(model.site_pos[site]))}
        return {'robot_model': robot.model_path, 'robot_model_sha256': file_digest(robot.model_path),
                'mujoco_version': mujoco.__version__, 'arms': arms, 'chassis': footprint,
                'radius_m': max(a['maximum_reach_m'] for a in arms.values()),
                'spacing_m': footprint['radius_m'] / 4,
                'reach_definition': 'maximum shoulder pivot to TCP distance within arm joint limits',
                'scope': 'free-space FK geometry only; no collision-free grasp or task-success claim',
                'physics_steps': 0}
    finally:
        sim.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-model', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = measure(args.robot_model)
    config = load_no_edit_config(args.config)
    for key in ('radius_m', 'spacing_m'):
        if not np.isclose(getattr(config, key), result[key], rtol=0, atol=1e-12):
            raise ValueError(f'Preset/model mismatch: {key}; remeasure before running')
    result['config'] = str(args.config.resolve())
    result['config_sha256'] = file_digest(args.config)
    result['disk_station_count'] = len(disk_samples([0, 0, 0], config.radius_m, config.spacing_m))
    write_json(args.output, result)
    print(f"Verified: radius={result['radius_m']:.12f} m, spacing={result['spacing_m']:.12f} m, "
          f"{result['disk_station_count']} lattice stations")


if __name__ == '__main__':
    main()
