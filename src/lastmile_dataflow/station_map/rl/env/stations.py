"""Spatial-block station split and physical Home initialization checks."""

from copy import deepcopy
from itertools import groupby
import math

import mujoco
import numpy as np

from lastmile_dataflow.sim.simulation import Simulation
from lastmile_dataflow.sim.model.geometry import floor_support, room_triangles
from lastmile_dataflow.sim.model.geometry import body_pose
from lastmile_dataflow.scenes.tasks import local_bounds
from lastmile_dataflow.scenes.tasks import disk_samples
from lastmile_dataflow.sim.placement import robot_contacts, place_frozen_station
from lastmile_dataflow.operation.protocol import SimulationState
from .scene import RLSceneConfig
from lastmile_dataflow.sim.model.geometry import descendants, geom_points


def station_candidates(center, settings):
    """Enumerate the grid, or keep one explicit world pose independent of grid IDs."""
    fixed = settings.get("fixed_station")
    if fixed:
        x, y, yaw = fixed["base_xyyaw"]
        if not np.isfinite([x, y, yaw]).all():
            raise ValueError("fixed_station.base_xyyaw must contain finite x, y, yaw")
        return [
            {
                "station_id": fixed["station_id"],
                "xy_id": fixed["station_id"].rsplit("-y", 1)[0],
                "x": float(x),
                "y": float(y),
                "yaw": float(yaw),
                "yaw_offset_rad": None,
                "split": "train",
                "valid": True,
                "invalid_reason": "",
            }
        ]

    points = disk_samples(center, settings["radius_m"], settings["spacing_m"])
    offsets = np.deg2rad(settings["heading_offsets_deg"])
    rows = []
    for point in points:
        xy = np.asarray(point["xy"])
        delta = center[:2] - xy
        common = {"xy_id": point["xy_id"], "x": float(xy[0]), "y": float(xy[1]), "split": "train"}
        if np.hypot(*delta) < 1e-9:
            rows.append(
                {
                    **common,
                    "station_id": f"{point['xy_id']}-nh",
                    "yaw": 0.0,
                    "yaw_offset_rad": None,
                    "valid": False,
                    "invalid_reason": "no_heading",
                }
            )
            continue
        facing = math.atan2(delta[1], delta[0])
        for index, offset in enumerate(offsets):
            yaw = math.atan2(math.sin(facing + offset), math.cos(facing + offset))
            rows.append(
                {
                    **common,
                    "station_id": f"{point['xy_id']}-y{index:02d}",
                    "yaw": yaw,
                    "yaw_offset_rad": float(offset),
                    "valid": True,
                    "invalid_reason": "",
                }
            )
    return rows


def prepare_stations(model, prepared, config):
    """CPU preparation only, before GPU episodes; no IK, FOV or reach filter."""

    scene = RLSceneConfig(**config["scene"])
    robot_config = scene.robot_config(entity=True)
    sim = Simulation(model, robot_config, prepared.catalog, prepared.target_body)
    parked = deepcopy(robot_config.initial)
    parked["base_xyyaw"] = [-10.0, -10.0, 0.0]
    sim.robot.initialize(parked)
    for _ in range(round(config["environment"]["settling_s"] / scene.physics_dt)):
        sim.robot.hold()
        mujoco.mj_step(model, sim.data)
    mujoco.mj_forward(model, sim.data)
    initial_qpos = sim.data.qpos.copy()
    rows = check_stations(sim, config["stations"])
    sim.close()
    if config["stations"].get("fixed_station") and not rows[0]["valid"]:
        raise ValueError(f"Fixed station {rows[0]['station_id']} is invalid: {rows[0]['invalid_reason']}")
    return rows, initial_qpos


def check_stations(sim, settings, *, facing_only=False):
    """Check sampled poses in a prepared model, restoring the complete baseline state."""
    model = sim.model
    robot_config = sim.robot.config
    state = SimulationState.capture(sim)
    # Each station uses the same settled objects, never the previous station's state.
    # 0 deg faces the settled target's collision-bounds centre; the grid is centred there too.
    lo, hi = local_bounds(sim, sim.target_id)
    center = (body_pose(sim, sim.target_id) @ np.r_[(lo + hi) / 2, 1.0])[:3]
    rows = station_candidates(center, settings)
    if facing_only:
        rows = [row for row in rows if row["yaw_offset_rad"] in (None, 0.0)]
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
    for _, group in groupby(rows, key=lambda row: row["xy_id"]):
        station_rows = list(group)
        xy = np.array([station_rows[0]["x"], station_rows[0]["y"]])
        # The conservative circular footprint does not depend on yaw.
        footprint = [
            xy,
            *[
                xy + radius * np.array([math.cos(a), math.sin(a)])
                for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)
            ],
        ]
        supported = all(floor_support(sim, p, triangles=triangles) for p in footprint)
        for row in station_rows:
            if not row["valid"]:
                continue
            initial = deepcopy(robot_config.initial)
            initial["base_xyyaw"] = [row["x"], row["y"], row["yaw"]]
            state.initialize(sim)
            place_frozen_station(sim, {"initial": initial})
            collisions = robot_contacts(sim)
            reason = "home_penetration" if collisions else "" if supported else "floor_support"
            row.update(valid=not reason, invalid_reason=reason)
    state.initialize(sim)
    return rows
