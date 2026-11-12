"""Uniform disk samples and pre-begin, robot-only visible initialization."""

import copy
from contextlib import closing, nullcontext
import mujoco
import numpy as np

from lastmile_dataflow.scenes.dataset import open_scene
from lastmile_dataflow.scenes.edits.search import task_geometry

from ...data.rendering.head_views import TaskVisibility
from lastmile_dataflow.scenes.geometry import floor_support, room_triangles
from lastmile_dataflow.runtime.construction.preparation import clone_preparation
from lastmile_dataflow.sim.placement import _restore_object_velocity, robot_contacts


def initialize_station(sim, station, anchor, *, floor_triangles=None):
    if sim.started or sim.closed:
        raise RuntimeError("station initialization only before begin")
    original = sim.data.qpos.copy()
    original_velocity = sim.data.qvel.copy()
    initial = copy.deepcopy(station.get("initial", sim.robot.config.initial))
    xy = np.asarray(station["xy"])
    explicit_yaw = station.get("yaw")
    yaw = explicit_yaw if explicit_yaw is not None else float(np.arctan2(anchor[1] - xy[1], anchor[0] - xy[0]))
    initial[sim.robot.initial_base_field] = [*xy, yaw]
    if explicit_yaw is None:
        yaw_adjustments = sim.robot.aim_station(initial, anchor)
    else:
        sim.robot.initialize(initial)
        yaw_adjustments = []
    _restore_object_velocity(sim, original_velocity)
    mujoco.mj_forward(sim.model, sim.data)
    robot_q = set(sim.robot.joints.addresses.values())
    mask = [i for i in range(sim.model.nq) if i not in robot_q]
    if not np.array_equal(original[mask], sim.data.qpos[mask]):
        raise RuntimeError("initialization changed non-robot qpos")
    collisions = robot_contacts(sim)
    ground = floor_support(sim, xy, triangles=floor_triangles)
    finite = bool(np.isfinite(sim.data.qpos).all() and np.isfinite(sim.data.qvel).all())
    row = {
        **station,
        "base": sim.robot.group("base").tolist(),
        "observation_camera": sim.robot.observation_camera,
        "initial": initial,
        "geometry": "valid" if ground and finite and not collisions else "geometry_filtered",
        "ground": ground,
        "collisions": collisions,
        "non_robot_qpos_unchanged": True,
        "visibility": None,
        "initial_yaw_adjustments": yaw_adjustments,
    }
    return row


def select_task_stations(sim, geometry, config, limit, *, measure_visibility=True):
    """Coverage order over the free lattice; only valid initial poses consume the ``limit`` slots."""
    free_lattice = [
        row
        for row in geometry.lattice
        if (cell := geometry.navigation.cell(row["xy"])) is not None and not geometry.occupancy.occupied[cell]
    ]
    covered, _ = coverage_select(
        [{**row, "geometry": "valid"} for row in free_lattice],
        geometry.task["anchor_world"],
        limit,
    )
    order = covered + free_lattice
    seen, selected, checked = set(), [], []
    initializations = 0
    floor_triangles = room_triangles(sim)
    visibility_context = TaskVisibility(sim, geometry.task, config) if measure_visibility else nullcontext(None)
    with visibility_context as visibility:
        for candidate in order:
            identity = candidate["station_id"]
            if identity in seen:
                continue
            seen.add(identity)
            initializations += 1
            with closing(clone_preparation(sim)) as probe:
                warnings = [warning.number for warning in probe.data.warning]
                try:
                    row = initialize_station(
                        probe, candidate, geometry.task["anchor_world"], floor_triangles=floor_triangles
                    )
                except ValueError as exc:
                    row = {**candidate, "geometry": "geometry_filtered", "reason": str(exc)}
                fresh = [
                    {"index": i, "number": int(w.number)}
                    for i, w in enumerate(probe.data.warning)
                    if w.number > warnings[i]
                ]
                if fresh:
                    row.update(geometry="geometry_filtered", reason="robot_initialization_solver_warning")
                row.update(
                    new_solver_warnings=fresh,
                    independent_simulation=True,
                    initialization_attempt_id=identity,
                    check_scope="actual_robot_initialization_and_three_camera_roi",
                )
                if row["geometry"] == "valid" and visibility is not None:
                    row["visibility"] = visibility.measure(probe)
            checked.append(row)
            if row["geometry"] == "valid":
                selected.append(row)
            if len(selected) == limit:
                break
    return selected, {
        "candidates_checked": len(checked),
        "selected_count": len(selected),
        "new_initializations": initializations,
        "reused_initializations": len(checked) - initializations,
        "pose_budget_fraction": len(selected) / limit,
        "lattice_count": len(geometry.lattice),
        "free_lattice_count": len(free_lattice),
        "visibility_measured": measure_visibility,
        "selected_ids": [row["station_id"] for row in selected],
        "priority": "coverage",
        "rejected_stations": [row for row in checked if row["geometry"] != "valid"],
    }


def _add_coverage_index(selected, limit, index):
    index = int(index)
    if len(selected) < limit and index not in selected:
        selected.append(index)


def coverage_select(stations, anchor, limit=20):
    valid = [s for s in stations if s["geometry"] == "valid"]
    if type(limit) is not int or limit <= 0:
        raise ValueError("positive station limit required")
    if len({s["station_id"] for s in valid}) != len(valid):
        raise ValueError("duplicate station identity")
    if not valid:
        return [], {"method": "bearing_range_stratified_farthest_point", "input_count": 0, "final_count": 0}
    delta = np.array([s["xy"] for s in valid]) - np.asarray(anchor[:2])
    radius = np.linalg.norm(delta, axis=1)
    theta = np.arctan2(delta[:, 1], delta[:, 0])
    rnorm = radius / max(float(radius.max()), 1e-9)
    features = np.column_stack((delta / max(float(radius.max()), 1e-9), np.cos(theta), np.sin(theta), 2 * rnorm))
    # Seed with both range extremes, then one representative per angular/radial bin.
    selected = []
    _add_coverage_index(selected, limit, np.argmin(radius))
    _add_coverage_index(selected, limit, np.argmax(radius))
    sector = np.minimum(7, ((theta + np.pi) / (2 * np.pi) * 8).astype(int))
    radial = np.minimum(1, (rnorm * 2).astype(int))
    for band in range(2):
        for angle in range(8):
            ids = np.flatnonzero((sector == angle) & (radial == band))
            if len(ids):
                center = features[ids].mean(axis=0)
                _add_coverage_index(selected, limit, ids[np.argmin(np.linalg.norm(features[ids] - center, axis=1))])
    while len(selected) < min(limit, len(valid)):
        distance = np.min(np.linalg.norm(features[:, None, :] - features[selected][None, :, :], axis=2), axis=1)
        distance[selected] = -1
        _add_coverage_index(selected, limit, np.argmax(distance))
    # Do not shuffle small sets unnecessarily; still no [:limit] selection.
    kept = valid if len(valid) <= limit else [valid[i] for i in selected]
    ids = {s["station_id"] for s in kept}
    discarded = [
        {"station_id": s["station_id"], "reason": "coverage_compression"} for s in valid if s["station_id"] not in ids
    ]
    return kept, {
        "method": "bearing_range_stratified_farthest_point",
        "input_count": len(valid),
        "final_count": len(kept),
        "discarded": discarded,
        "selected_ids": [s["station_id"] for s in kept],
        "range_m": [float(radius.min()), float(radius.max())],
        "angular_bins": 8,
        "radial_bins": 2,
        "features": "normalized_xy, cos_bearing, sin_bearing, 2*normalized_range",
    }


def sample_scene(scene, station_config, limit):
    """Candidate stations of one library scene (edited scenes also measure target visibility)."""
    with open_scene(scene) as sim:
        try:
            geometry = task_geometry(sim, scene["task"], station_config)
        except ValueError as exc:
            return {"scene_id": scene["scene_id"], "status": "rejected", "reason": str(exc)}
        stations, sampling = select_task_stations(
            sim, geometry, station_config, limit, measure_visibility=scene["proposal_intent"] == "edit"
        )
    for station in stations:
        cell = geometry.navigation.cell(station["xy"])
        station["clearance_base_m"] = float(geometry.clearance_base[cell]) if cell is not None else None
    row = {"scene_id": scene["scene_id"], "status": "accepted" if stations else "rejected"}
    if not stations:
        row["reason"] = "no_valid_initial_stations"
    return {**row, "stations": stations, "station_sampling": sampling}
