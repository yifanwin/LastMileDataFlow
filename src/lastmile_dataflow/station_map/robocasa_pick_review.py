"""Recheck frozen RoboCasa variants using shared stations and native camera views."""

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from lastmile_dataflow.robots.registry import load_robot_config
from lastmile_dataflow.sim.model.geometry import collision_geoms
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.utils.io import read_json, rows, write_json, write_rows
from .sampling.robocasa import check_robocasa_stations, robocasa_stations
from .robocasa_pick_figures import draw_details, draw_overview
from .robocasa_pick_views import RoboCasaPickViews

CAMERAS = ("robot0_agentview_left", "robot0_agentview_right")
logger = logging.getLogger(__name__)


def _representatives(nominal, geometry):
    origin, axes = np.asarray(geometry["origin_world"]), np.asarray(geometry["axes_world"])
    valid = [row for row in nominal if row["valid"]]
    result = {}
    for name, cross in (("Left", -0.33), ("Front", 0), ("Right", 0.33)):
        desired = (origin + axes @ [cross, -0.66, 0])[:2]
        result[name] = min(valid, key=lambda row: np.linalg.norm([row["x"], row["y"]] - desired))["station_id"]
    return result


def _shared_state(runtime):
    sim = runtime.env.sim
    return {
        name: np.r_[sim.data.get_joint_qpos(name), sim.data.get_joint_qvel(name)]
        for name in sim.model.joint_names
    }


def _check_frozen(runtime, parent_joints, row):
    sim = runtime.env.sim
    record = {
        "flattened_state_max_abs_error": float(np.abs(sim.get_state().flatten() - runtime.initial_state).max()),
        "shared_joint_max_abs_error": 0.0,
        "target_pose_xyz_wxyz": runtime.get_state().target_pose,
        "base_xyyaw": runtime.base_pose(),
        "cup_support_contacts": runtime.support_contacts(),
    }
    current = _shared_state(runtime)
    record["shared_joint_max_abs_error"] = max(
        float(np.abs(q - current[name]).max()) for name, q in parent_joints.items()
    )
    if record["flattened_state_max_abs_error"] > 1e-10 or record["shared_joint_max_abs_error"] > 1e-10:
        raise ValueError(f"{row['id']} changed a frozen joint state")
    if row["kind"] == "original":
        return record
    obstacle = row["obstacle"]
    body = sim.model.body(obstacle["body"]).id
    reloaded_pose = np.r_[sim.data.xpos[body], sim.data.xquat[body]]
    geom_ids = collision_geoms(sim, body)
    geom_names = [sim.model.geom(g).name for g in geom_ids]
    expected_pose = row.get("frozen_pose_xyz_wxyz", row["settled_pose_xyz_wxyz"])
    pose_error = float(np.max(np.abs(reloaded_pose - expected_pose)))
    if pose_error > 1e-10 or geom_names != obstacle["collision_geoms"]:
        raise ValueError(f"{row['id']} obstacle changed on reload")
    if row["kind"] == "box" and not np.allclose(sim.model.geom_size[geom_ids[0]], obstacle["mjcf_box_half_size_m"]):
        raise ValueError(f"{row['id']} half-size changed on reload")
    return {**record, "obstacle_pose_max_abs_error": pose_error, "collision_geoms": geom_names}


def _measure_variant(runtime, rows_by_station, ordered, output, views, obstacle_body, min_pixels, min_fraction):
    captured = {}
    for row in rows_by_station:
        if not row["valid"]:
            row.update(pi05_visible=False, cameras={})
            continue
        runtime.probe_station(row["x"], row["y"], row["yaw"])
        row["cameras"] = {
            name: views.measure(name, min_pixels=min_pixels, min_fraction=min_fraction) for name in CAMERAS
        }
        row["pi05_visible"] = any(x["status"] == "visible" for x in row["cameras"].values())
        if row["station_id"] in ordered.values():
            for camera in CAMERAS:
                image = runtime.render(camera)
                path = output / f"{row['station_id']}-{camera.rsplit('_', 1)[-1]}.png"
                Image.fromarray(image).save(path)
                captured[(row["station_id"], camera)] = {
                    "path": path, "obstacle_pixels": views.obstacle_pixels(camera, obstacle_body) if obstacle_body else 0,
                }
    return captured


def _verify_representative(scene, rows_by_station, ordered, images):
    row = next(row for row in rows_by_station if row["station_id"] == ordered["Front"])
    if not row["valid"]:
        row = next(row for row in rows_by_station if row["valid"] and row["station_id"] in ordered.values())
    runtime = RoboCasaRuntime(scene)
    try:
        runtime.set_station(row["x"], row["y"], row["yaw"])
        difference = {}
        for camera in CAMERAS:
            path = images[(row["station_id"], camera)]["path"]
            reference = np.array(Image.open(path))
            replay = runtime.render(camera)
            difference[camera] = int(np.abs(replay.astype(int) - reference).max())
        # The renderer can round a channel differently by one 8-bit level across contexts.
        if max(difference.values()) > 1:
            raise ValueError(f"{scene} representative RGB changed on reload: {difference}")
        return {"station_id": row["station_id"], "max_rgb_difference": difference}
    finally:
        runtime.close()


def _review_scene(row, scene, nominal, representatives, geometry, parent_joints, output, figures, min_pixels, min_fraction):
    output.mkdir()
    runtime = RoboCasaRuntime(scene)
    obstacle_body = row.get("obstacle", {}).get("body")
    try:
        reload_checks = _check_frozen(runtime, parent_joints, row)
        views = RoboCasaPickViews(runtime)
        try:
            overhead, obstacle_pixels, cup_pixels = views.overhead(geometry, obstacle_body)
            overhead_path = output / "oblique-overhead.png"
            Image.fromarray(overhead).save(overhead_path)
            measured = check_robocasa_stations(runtime, nominal)
            captured = _measure_variant(
                runtime, measured, representatives, output, views, obstacle_body, min_pixels, min_fraction,
            )
        finally:
            views.close()
    finally:
        runtime.close()

    write_rows(output / "stations.jsonl", measured)
    reload_checks["overhead_obstacle_pixels"] = obstacle_pixels
    reload_checks["overhead_cup_pixels"] = cup_pixels
    reload_checks["representative_obstacle_pixels"] = {
        f"{station}:{camera}": item["obstacle_pixels"] for (station, camera), item in captured.items()
    }
    write_json(output / "reload.json", reload_checks)
    sets = {"legal": {q["station_id"] for q in measured if q["valid"]},
            "visible": {q["station_id"] for q in measured if q["pi05_visible"]}}
    visible_obstacle = bool(obstacle_pixels or any(item["obstacle_pixels"] for item in captured.values()))
    summary = {
        "id": row["id"], "kind": row["kind"], "scene": str(scene), "far_control": row.get("far_control"),
        "horizontal_gap_m": row.get("horizontal_gap_m"), "actual_center_distance_m": row.get("actual_center_distance_m"),
        "placement": row.get("placement"), "legal": len(sets["legal"]), "visible": len(sets["visible"]),
        "reviewable": bool(sets["legal"]) and (not obstacle_body or visible_obstacle),
    }
    figure = None
    if summary["reviewable"]:
        figure = draw_details(figures, summary, row, measured, representatives, geometry, overhead_path, captured)
    return summary, sets, measured, captured, figure


def _pair_counts(candidates, station_sets):
    for row in candidates:
        far = row["far_control"]
        if far in station_sets:
            own, control = station_sets[row["id"]], station_sets[far]
            row["shared_legal_with_far"] = len(own["legal"] & control["legal"])
            row["shared_visible_with_far"] = len(own["visible"] & control["visible"])

    groups = []
    available = {row["id"] for row in candidates if row["reviewable"]}
    for row in candidates:
        if not (row["placement"] or "").startswith("left-"):
            continue
        suffix = row["placement"].removeprefix("left-")
        right = f"{row['kind']}-right-{suffix}"
        members = [row["far_control"], row["id"], right]
        if all(key in available for key in members):
            groups.append({
                "kind": row["kind"], "placement_pair": suffix, "members": members,
                "shared_legal": len(set.intersection(*(station_sets[key]["legal"] for key in members))),
                "shared_visible": len(set.intersection(*(station_sets[key]["visible"] for key in members))),
            })
    return groups


def review_pick_variants(run, output, robot_config, *, min_pixels, min_fraction):
    """Reload every supported snapshot, retain per-camera evidence, and draw reviewable variants."""
    run, output = Path(run).absolute(), Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    figures = output / "details"
    figures.mkdir()
    evidence = output / "evidence"
    evidence.mkdir()
    geometry = read_json(run / "geometry.json")
    variants = rows(run / "scenes.jsonl")
    by_id = {}
    station_sets = {}
    robot = load_robot_config(robot_config)
    runtime = RoboCasaRuntime(variants[0]["scene"])
    try:
        parent_joints = _shared_state(runtime)
        nominal, grid = robocasa_stations(runtime, robot)
    finally:
        runtime.close()
    representatives = _representatives(nominal, geometry)
    write_rows(output / "nominal_stations.jsonl", nominal)
    write_json(output / "review_config.json", {
        "build_run": str(run), "grid": grid, "representatives": representatives, "cameras": CAMERAS,
        "min_reference_pixels": min_pixels, "min_visible_fraction": min_fraction, "require_full_projection_in_frame": True,
    })
    rgb_recheck = {}
    for row in variants:
        if row["status"] != "accepted":
            continue
        key = row["id"]
        logger.info("Reloading %s and probing shared stations", key)
        scene = run / row["scene"]
        summary, sets, measured, images, figure = _review_scene(
            row, scene, nominal, representatives, geometry, parent_joints, evidence / key,
            figures, min_pixels, min_fraction,
        )
        station_sets[key] = sets
        by_id[key] = summary, figure
        print(f"{key} legal={summary['legal']} visible={summary['visible']}", flush=True)
        kind = row["kind"]
        if kind != "original" and row["placement"] != "far" and images and kind not in rgb_recheck:
            rgb_recheck[kind] = {"id": key, **_verify_representative(scene, measured, representatives, images)}
            write_json(output / "independent_rgb_reload.json", rgb_recheck)

    summaries = [summary for summary, _ in by_id.values()]
    groups = _pair_counts(summaries, station_sets)
    for kind in ("kettle", "box"):
        draw_overview(output, kind, [entry for entry in by_id.values() if entry[0]["kind"] in ("original", kind)])
    write_json(output / "selection.json", {
        "candidates": summaries,
        "matched_groups": groups,
        "rejections": [{"id": row["id"], "reasons": row["reasons"]} for row in variants if row["status"] == "rejected"],
    })
    return output
