"""RL-grid station candidates for a frozen RoboCasa instance.

Same rule as the val103 π0.5 station experiment (``station_map/rl/env/stations.py``): a disk lattice
centred on the target's collision-bounds centre, radius = robot working reach, spacing = base radius / 4,
one heading per point facing that centre. A station is valid when the robot, in its frozen initial arm and
torso pose, touches nothing and its conservative circular footprint lies on the kitchen floor. No IK, FOV or
reach filter. Checks are static (``RoboCasaRuntime.probe_station``); trials still start with ``set_station``.
"""

import math

import numpy as np

from lastmile_dataflow.scenes.tasks import facing_stations


def _geom_corners(model, data, geom):
    center, half = model.geom_aabb[geom, :3], model.geom_aabb[geom, 3:]
    signs = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)
    return data.geom_xpos[geom] + (center + signs * half) @ data.geom_xmat[geom].reshape(3, 3).T


def target_center(runtime):
    """World centre of the target's contact-geom bounds at the frozen state."""
    model, data = runtime.env.sim.model._model, runtime.env.sim.data._data
    corners = np.concatenate([_geom_corners(model, data, g) for g in sorted(runtime.target_geoms)])
    return (corners.min(axis=0) + corners.max(axis=0)) / 2


def footprint_radius(runtime):
    """Largest XY distance of mobile-base collision geometry (support column excluded) from the base origin."""
    model, data = runtime.env.sim.model._model, runtime.env.sim.data._data
    base, support = model.body("mobilebase0_base").id, model.body("mobilebase0_support").id
    bodies = _subtree(model, base) - _subtree(model, support)
    geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] in bodies and (model.geom_contype[g] or model.geom_conaffinity[g])]
    corners = np.concatenate([_geom_corners(model, data, g) for g in geoms])
    return float(np.linalg.norm(corners[:, :2] - data.xpos[base, :2], axis=1).max())


def floor_rectangles(runtime):
    """World XY bounds of the kitchen floor geoms (bodies named ``floor*``)."""
    model, data = runtime.env.sim.model._model, runtime.env.sim.data._data
    rectangles = []
    for g in range(model.ngeom):
        if model.body(model.geom_bodyid[g]).name.startswith("floor") and (model.geom_contype[g] or model.geom_conaffinity[g]):
            corners = _geom_corners(model, data, g)
            rectangles.append((*corners[:, :2].min(axis=0), *corners[:, :2].max(axis=0)))
    return rectangles


def _subtree(model, root):
    result = {root}
    for body in range(root + 1, model.nbody):
        if int(model.body_parentid[body]) in result:
            result.add(body)
    return result


def _on_floor(point, rectangles):
    return any(x0 <= point[0] <= x1 and y0 <= point[1] <= y1 for x0, y0, x1, y1 in rectangles)


def robocasa_stations(runtime, robot):
    """All lattice candidates with validity; leaves the runtime at a probe state (call ``reset``/``set_station``)."""
    center = target_center(runtime)
    radius = footprint_radius(runtime)
    floor = floor_rectangles(runtime)
    spacing = robot.base_radius_m / 4
    nominal = facing_stations(center, robot.arm_working_reach_m, spacing)
    rows = _check_stations(runtime, nominal, radius, floor)
    meta = {
        "rule": "rl-grid facing-target (val103): radius=arm_working_reach_m, spacing=base_radius_m/4",
        "center": center.tolist(),
        "radius_m": robot.arm_working_reach_m,
        "spacing_m": spacing,
        "footprint_radius_m": radius,
        "floor_rectangles": len(floor),
        "candidates": len(rows),
        "valid": sum(r["valid"] for r in rows),
    }
    return rows, meta


def check_robocasa_stations(runtime, nominal):
    """Recheck the same XY/yaw table on a reloaded variant, including previously rejected points."""
    return _check_stations(runtime, nominal, footprint_radius(runtime), floor_rectangles(runtime))


def _check_stations(runtime, nominal, radius, floor):
    angles = np.linspace(0, 2 * math.pi, 16, endpoint=False)
    footprint_offsets = np.vstack((np.zeros(2), radius * np.column_stack((np.cos(angles), np.sin(angles)))))
    rows = [dict(row) for row in nominal]
    for row in rows:
        if row["yaw_offset_rad"] is None:
            continue
        xy = np.array([row["x"], row["y"]])
        contacts = runtime.probe_station(*xy, row["yaw"])
        reason = "home_penetration" if contacts else "" if all(_on_floor(p, floor) for p in xy + footprint_offsets) else "floor_support"
        row.update(valid=not reason, invalid_reason=reason, contacts=contacts)
    return rows
