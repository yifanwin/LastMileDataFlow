"""Task discovery from the actual exported model, not historical success labels."""

from lastmile_dataflow.operation.grasps import load_grasps

from pathlib import Path
from urllib.parse import quote
import mujoco
import numpy as np

from lastmile_dataflow.sim.model.geometry import body_points, body_pose, descendants, geom_points
import math
from lastmile_dataflow.sim.model.geometry import collision_geoms


LABELS = {
    "Cup": "杯子",
    "Mug": "马克杯",
    "Bottle": "瓶子",
    "Bowl": "碗",
    "Plate": "盘子",
    "Book": "书",
    "Apple": "苹果",
    "CellPhone": "手机",
    "Fridge": "冰箱",
    "Microwave": "微波炉",
    "Dresser": "抽屉柜",
    "Desk": "书桌",
    "SideTable": "边桌",
    "Safe": "保险柜",
    "Doorway": "房门",
}
OPEN_TYPES = {
    "Doorway",
    "Door",
    "Fridge",
    "Microwave",
    "Dresser",
    "Desk",
    "SideTable",
    "Cabinet",
    "Drawer",
    "Safe",
    "WashingMachine",
    "ClothesDryer",
}


def local_bounds(sim, body):
    points = body_points(sim, body)
    transform = body_pose(sim, body)
    local = (points - transform[:3, 3]) @ transform[:3, :3]
    return local.min(axis=0), local.max(axis=0)


def _is_free(model, body):
    start = int(model.body_jntadr[body])
    return any(
        model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE for j in range(start, start + int(model.body_jntnum[body]))
    )


def _handle_body(sim, moving, body_semantics):
    """First body under the moving part named or annotated as a handle, else the part itself."""
    handles = [
        body
        for body in sorted(descendants(sim.model, moving))
        if "handle" in sim.model.body(body).name.lower()
        or "handle" in str(body_semantics.get(sim.model.body(body).name, "")).lower()
    ]
    return (handles[0], True) if handles else (moving, False)


def _open_task(sim, j, joint_name, obj, common, instruction, config, skipped):
    """Open task for one limited hinge/slide joint, or None when not eligible."""
    model = sim.model
    name = common["instance_id"]
    moving = int(model.jnt_bodyid[j])
    handle, explicit = _handle_body(sim, moving, obj.get("name_map", {}).get("bodies", {}))
    try:
        hlo, hhi = local_bounds(sim, handle)
    except ValueError:
        return None
    hp = body_pose(sim, handle)
    contact = hp[:3, :3] @ ((hlo + hhi) / 2) + hp[:3, 3]
    q0 = float(sim.data.qpos[model.jnt_qposadr[j]])
    low, high = map(float, model.jnt_range[j])
    # Closed end nearest to zero; do not change the source articulation pose.
    closed, opened = (low, high) if abs(low) <= abs(high) else (high, low)
    travel = abs(opened - closed)
    if travel < 1e-4 or abs(q0 - closed) / travel >= config.open_fraction:
        skipped.append({"instance": name, "joint": joint_name, "reason": "already_open_or_no_travel"})
        return None
    unit = "slide" if int(model.jnt_type[j]) == mujoco.mjtJoint.mjJNT_SLIDE else "hinge"
    threshold = min(
        travel, max(config.open_fraction * travel, config.open_slide_m if unit == "slide" else config.open_hinge_rad)
    )
    target_q = closed + np.sign(opened - closed) * threshold
    if abs(target_q - q0) < 1e-4:
        return None
    return {
        **common,
        "operation": "open",
        "joint_name": joint_name,
        "joint_kind": unit,
        "joint_initial": q0,
        "joint_goal": float(target_q),
        "joint_range": [low, high],
        "joint_axis_world": sim.data.xaxis[j].tolist(),
        "joint_anchor_world": sim.data.xanchor[j].tolist(),
        "target_body": model.body(moving).name,
        "handle_body": model.body(handle).name,
        "explicit_handle": explicit,
        "handle_local_bounds": [hlo.tolist(), hhi.tolist()],
        "anchor_world": contact.tolist(),
        "instruction": instruction + ("抽屉。" if unit == "slide" else "门。"),
        "task_id": f"open-{quote(name, safe='')}-{quote(joint_name, safe='')}",
    }


def _open_tasks(sim, obj, common, qualifier, label, config, skipped):
    tasks = []
    for joint_name, semantic in sorted(obj.get("name_map", {}).get("joints", {}).items()):
        try:
            j = sim.model.joint(joint_name).id
        except KeyError:
            continue
        kind = int(sim.model.jnt_type[j])  # numpy int vs pybind enum: `in` needs a Python int
        if kind not in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE):
            continue
        semantic = (str(semantic) + " " + joint_name).lower()
        if "handle" in semantic or not sim.model.jnt_limited[j]:
            continue
        task = _open_task(
            sim, j, joint_name, obj, common, f"打开{qualifier}的{label}的第{len(tasks) + 1}个", config, skipped
        )
        if task is not None:
            tasks.append(task)
    return tasks


def discover_tasks(sim, metadata, config):
    tasks, skipped = [], []
    size_limit = getattr(config, "max_pick_object_size_m", None) or config.gripper_width_m
    for instance in sim.catalog:
        name, asset, category = instance["instance_id"], instance.get("asset_id"), instance.get("category")
        if not asset or name.startswith("robot_0/"):
            continue
        b = instance["body_id"]
        try:
            lo, hi = local_bounds(sim, b)
        except ValueError:
            skipped.append({"instance": name, "reason": "missing_collision_geometry"})
            continue
        pose = body_pose(sim, b)
        center = pose[:3, :3] @ ((lo + hi) / 2) + pose[:3, 3]
        label = LABELS.get(category, category or asset)
        source_id = instance.get("source_object_id") or name
        # Unique instance qualifier is necessary for repeated same-category objects.
        qualifier = f"房间{instance.get('room_id')}中编号{source_id}"
        common = {
            "instance_id": name,
            "asset_id": asset,
            "category": category,
            "room_id": instance.get("room_id"),
            "dimensions_m": (hi - lo).tolist(),
            "source_object_id": source_id,
            "center_world": center.tolist(),
        }
        if "pick" in config.operations and min(hi - lo) <= size_limit + 1e-9 and _is_free(sim.model, b):
            tasks.append(
                {
                    **common,
                    "operation": "pick",
                    "target_body": name,
                    "instruction": f"拿起{qualifier}的{label}。",
                    "anchor_world": center.tolist(),
                    "local_bounds": [lo.tolist(), hi.tolist()],
                    "task_id": f"pick-{quote(name, safe='')}",
                }
            )
        elif "pick" in config.operations and min(hi - lo) <= size_limit:
            skipped.append({"instance": name, "operation": "pick", "reason": "not_free_moving_instance"})
        if "open" not in config.operations or category not in OPEN_TYPES:
            continue
        opened = _open_tasks(sim, metadata.get("objects", {}).get(name, {}), common, qualifier, label, config, skipped)
        tasks.extend(opened)
        if not opened:
            skipped.append({"instance": name, "operation": "open", "reason": "no_eligible_articulation"})
    return tasks, skipped


def _dataset_grasps(task, assets_dir, max_rows, empty_sources):
    """Orthonormalized rows of the first non-empty asset grasp file."""
    asset = task["asset_id"]
    for directory in ("droid", "droid_objaverse"):
        path = Path(assets_dir) / "grasps" / directory / asset / (asset + "_grasps_filtered.npz")
        if not path.is_file():
            continue
        result = load_grasps(path, task["target_body"], max_rows)
        if not result:
            empty_sources.append(str(path))
        if result:
            return result
    return []


def _pinch_grasps(task, pinch_inset_m, empty_sources):
    lo, hi = np.asarray(task.get("handle_local_bounds", task.get("local_bounds")))
    center = (lo + hi) / 2
    width_axis = int(np.argmin(hi - lo))
    size = hi - lo
    # The bbox centre sits inside the handle; the Panda palm is 3.1 cm behind
    # the TCP, so an open grasp there always interpenetrates the handle. Place
    # the pinch point on the robot-facing surface and let it sit `pinch_inset_m`
    # inside: the open path retreats by approach_offset_m (1 cm), so the net
    # depth is 1 cm, enough for the 3 cm finger band to close on the handle.
    # Pick keeps the centre: its corpus poses are already on the object surface.
    open_pinch = task["operation"] == "open"
    result = []
    for axis in range(3):
        if axis == width_axis:
            continue
        for sign in (-1, 1):
            y = np.eye(3)[width_axis]
            z = sign * np.eye(3)[axis]
            x = np.cross(y, z)
            depth = float(np.abs(z) @ size)
            point = center - z * (depth / 2 - pinch_inset_m) if open_pinch else center
            for flip in (1, -1):
                pose = np.eye(4)
                pose[:3, :3] = np.column_stack((flip * x, flip * y, z))
                pose[:3, 3] = point
                result.append(
                    {
                        "pose_local": pose,
                        "body": task.get("handle_body", task["target_body"]),
                        "source": "procedural_bbox_pinch_v1",
                        "row": len(result),
                        "empty_asset_grasp_sources": empty_sources,
                    }
                )
    return result


def grasp_candidates(task, assets_dir, max_rows=1024, pinch_inset_m=0.020, *, allow_fallback=True):
    """Read asset grasp poses, or generate bbox pinch poses without modifying assets."""
    asset = task["asset_id"]
    if Path(asset).name != asset or asset in (".", ".."):
        raise ValueError("unsafe asset identity")
    empty_sources = []
    if task["operation"] == "pick":
        result = _dataset_grasps(task, assets_dir, max_rows, empty_sources)
        if result:
            return result
        if not allow_fallback or task.get("require_data_grasps"):
            return []
    return _pinch_grasps(task, pinch_inset_m, empty_sources)


def disk_samples(anchor, radius, spacing):
    if not np.isfinite([*anchor[:2], radius, spacing]).all() or radius <= 0 or spacing <= 0:
        raise ValueError("invalid disk lattice")
    result = []
    extent = math.floor(radius / spacing)
    for iy in range(-extent, extent + 1):
        for ix in range(-extent, extent + 1):
            delta = spacing * np.array([ix, iy], float)
            if np.dot(delta, delta) > radius**2 + 1e-9:
                continue
            xy = np.asarray(anchor[:2]) + delta
            identity = f"S{len(result):04d}"
            result.append(
                {
                    "station_id": identity,
                    "xy_id": identity,
                    "xy": xy.tolist(),
                    "grid_index": [ix, iy],
                    "yaw_source": "target_facing",
                }
            )
    return result


def task_support_obstacles(sim, task):
    """Conservative support envelopes from actual geometry, not success labels.

    Parent metadata is a hint only: require an overlapping surface immediately
    below the target. Without a hint, search collision surfaces under the target.
    Open tasks reserve their articulated furniture instance instead.
    """
    target = sim.model.body(task["target_body"]).id
    target_bodies = descendants(sim.model, target)
    target_lo = body_points(sim, target).min(axis=0)
    anchor = np.asarray(task["anchor_world"])
    hint = next((e.get("parent_instance_id") for e in sim.catalog if e["instance_id"] == task["instance_id"]), None)
    candidates = []
    for entry in sim.catalog:
        is_open = task["operation"] == "open" and entry["instance_id"] == task["instance_id"]
        if (entry["body_id"] in target_bodies and not is_open) or entry["mjcf_body"].startswith("robot_0/"):
            continue
        points = [
            geom_points(sim, g)
            for g in collision_geoms(sim, entry["body_id"])
            if is_open or int(sim.model.geom_bodyid[g]) not in target_bodies
        ]
        if not points:
            continue
        tops = []
        for pts in points:
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            if np.all(anchor[:2] >= lo[:2]) and np.all(anchor[:2] <= hi[:2]) and abs(hi[2] - target_lo[2]) <= 0.05:
                tops.append(hi[2])
        if not tops and not is_open:
            continue
        pts = np.concatenate(points)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        obstacle = {
            "body": entry["mjcf_body"],
            "min": lo.tolist(),
            "max": hi.tolist(),
            "kind": "support",
            "evidence": "open_task_furniture" if is_open else "surface_below_target_AABB",
        }
        candidates.append((is_open or entry["instance_id"] == hint, max(tops, default=hi[2]), obstacle))
    if not candidates:
        return []
    return [max(candidates, key=lambda item: item[:2])[2]]
