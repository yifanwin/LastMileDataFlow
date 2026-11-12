"""Spatial-block station split and physical Home initialization checks."""

from copy import deepcopy
import math

import mujoco
import numpy as np

from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.sim.simulation import Simulation
from lastmile_dataflow.sim.model.geometry import floor_support, room_triangles
from lastmile_dataflow.sim.model.geometry import body_pose
from lastmile_dataflow.scenes.tasks import local_bounds
from lastmile_dataflow.scenes.tasks import disk_samples
from lastmile_dataflow.sim.placement import robot_contacts
from .scene import RLSceneConfig
from lastmile_dataflow.sim.model.geometry import descendants, geom_points


def spatial_split(points, block_m, fractions, seed):
    blocks = sorted({tuple(np.floor(np.asarray(p["xy"]) / block_m).astype(int)) for p in points})
    order = np.random.default_rng(seed).permutation(len(blocks))
    n_train = int(len(blocks) * fractions[0])
    n_validation = int(len(blocks) * fractions[1])
    labels = (
        ["train"] * n_train + ["validation"] * n_validation + ["acceptance"] * (len(blocks) - n_train - n_validation)
    )
    assignment = {blocks[i]: label for i, label in zip(order, labels)}
    return [assignment[tuple(np.floor(np.asarray(p["xy"]) / block_m).astype(int))] for p in points]


def prepare_stations(model, prepared, config):
    """CPU preparation only, before GPU episodes; no IK, FOV or reach filter."""

    scene = RLSceneConfig(**config["scene"])
    robot_config = PandaOmronConfig(
        model_path=scene.asset(scene.robot_xml),
        namespace="robot/robot_0/",
        physics_dt=scene.physics_dt,
        initial={
            "base_xyyaw": [0.0, 0.0, 0.0],
            "lift": scene.lift,
            "arm_q": {"right": list(scene.home)},
            "gripper_width": {},
        },
    )
    sim = Simulation(model, robot_config, prepared.catalog, prepared.target_body)
    parked = deepcopy(robot_config.initial)
    parked["base_xyyaw"] = [-10.0, -10.0, 0.0]
    sim.robot.initialize(parked)
    for _ in range(round(config["environment"]["settling_s"] / scene.physics_dt)):
        sim.robot.hold()
        mujoco.mj_step(model, sim.data)
    mujoco.mj_forward(model, sim.data)
    initial_qpos = sim.data.qpos.copy()
    # Each station uses the same settled objects, never the previous station's state.
    setting = config["stations"]
    # 0 deg faces the settled target's collision-bounds centre; the grid is centred there too.
    lo, hi = local_bounds(sim, sim.target_id)
    center = (body_pose(sim, sim.target_id) @ np.r_[(lo + hi) / 2, 1.0])[:3]
    offsets = np.deg2rad(setting["heading_offsets_deg"])
    points = disk_samples(center, setting["radius_m"], setting["spacing_m"])
    splits = spatial_split(points, setting["block_m"], setting["split"], setting["seed"])
    triangles = room_triangles(sim)

    base = model.body(robot_config.namespace + "mobilebase0_base").id
    support = model.body(robot_config.namespace + "mobilebase0_support").id
    bodies = set(descendants(model, base)) - set(descendants(model, support))
    points_base = np.concatenate(
        [
            geom_points(sim, g)
            for g in range(model.ngeom)
            if model.geom_bodyid[g] in bodies and (model.geom_contype[g] or model.geom_conaffinity[g])
        ]
    )
    radius = np.linalg.norm(
        points_base[:, :2] - sim.data.site_xpos[model.site(robot_config.namespace + "base_site").id, :2], axis=1
    ).max()
    rows = []
    for point, split in zip(points, splits):
        xy = np.asarray(point["xy"])
        # The conservative circular footprint does not depend on yaw.
        footprint = [
            xy,
            *[
                xy + radius * np.array([math.cos(a), math.sin(a)])
                for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)
            ],
        ]
        supported = all(floor_support(sim, p, triangles=triangles) for p in footprint)
        delta = center[:2] - xy
        if np.hypot(*delta) < 1e-9:
            rows.append(
                {
                    "station_id": f"{point['xy_id']}-nh",
                    "xy_id": point["xy_id"],
                    "x": float(xy[0]),
                    "y": float(xy[1]),
                    "yaw": 0.0,
                    "yaw_offset_rad": None,
                    "split": split,
                    "valid": False,
                    "invalid_reason": "no_heading",
                }
            )
            continue
        facing = math.atan2(delta[1], delta[0])
        for index, offset in enumerate(offsets):
            yaw = math.atan2(math.sin(facing + offset), math.cos(facing + offset))
            initial = deepcopy(robot_config.initial)
            initial["base_xyyaw"] = [*point["xy"], yaw]
            sim.data.qpos[:] = initial_qpos
            sim.robot.initialize(initial)
            collisions = robot_contacts(sim)
            reason = "home_penetration" if collisions else "" if supported else "floor_support"
            rows.append(
                {
                    "station_id": f"{point['xy_id']}-y{index:02d}",
                    "xy_id": point["xy_id"],
                    "x": float(xy[0]),
                    "y": float(xy[1]),
                    "yaw": yaw,
                    "yaw_offset_rad": float(offset),
                    "split": split,
                    "valid": not reason,
                    "invalid_reason": reason,
                }
            )
    sim.close()
    return rows, initial_qpos
