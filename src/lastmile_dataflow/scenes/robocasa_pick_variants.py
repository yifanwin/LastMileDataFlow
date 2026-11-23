"""Place one movable obstacle in a frozen RoboCasa kitchen and test it at rest."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
from scipy.spatial import ConvexHull

from lastmile_dataflow.sim.model.geometry import body_points, collision_geoms, geom_points, quat_angle, quat_yaw
from lastmile_dataflow.sim.robocasa_runtime import ENV_FILE, RoboCasaRuntime, write_frozen_scene
from lastmile_dataflow.utils.io import append_row, create_run, read_config, read_json, write_json


@dataclass
class CounterFrame:
    origin: np.ndarray
    axes: np.ndarray
    support: dict

    @classmethod
    def from_parent(cls, runtime, fixture):
        sim = runtime.env.sim
        fixture = runtime.env.fixtures[fixture].root_body
        axes = sim.data.body(fixture).xmat.reshape(3, 3).copy()
        points = body_points(sim, runtime.target_body)
        origin = (points.max(axis=0) + points.min(axis=0)) / 2
        # The fixture's local +Y goes inward from its front edge.
        names = runtime.support_contacts()
        origin[2] = max(geom_points(sim, sim.model.geom(name).id)[:, 2].max() for name in names)
        support = {}
        for geom in collision_geoms(sim, sim.model.body(fixture).id):
            world = geom_points(sim, geom)
            if np.isclose(world[:, 2].max(), origin[2], atol=1e-6):
                local = (world - origin) @ axes
                support[sim.model.geom(geom).name] = [local.min(axis=0)[:2], local.max(axis=0)[:2]]
        return cls(origin, axes, support)

    def covers(self, local_points, inset):
        low, high = local_points[:, :2].min(axis=0), local_points[:, :2].max(axis=0)
        x, y = np.meshgrid(*(np.linspace(a - inset, b + inset, 9) for a, b in zip(low, high)))
        samples = np.column_stack((x.ravel(), y.ravel()))
        return all(any(np.all((lo <= point) & (point <= hi)) for lo, hi in self.support.values()) for point in samples)


def _mesh_obstacle(parent, asset):
    # Use the same asset resolver and object assembly as Kitchen._create_objects.
    from robocasa.utils.env_utils import create_obj

    cfg = deepcopy(next(obj for obj in parent.ep_meta["object_cfgs"] if obj["name"] == "distr_counter"))
    cfg.update(name="pick_kettle", info={"mjcf_path": asset})
    # The playback reset constructs a temporary sampled placement before loading the frozen state.
    cfg["placement"].update(ensure_valid_placement=False, ensure_object_boundary_in_range=False)
    obj, cfg["info"] = create_obj(parent.env, cfg)
    xml = ET.fromstring(parent.xml)
    xml.find("asset").extend(deepcopy(list(obj.asset)))
    xml.find("worldbody").append(deepcopy(obj.get_obj()))
    meta = deepcopy(parent.ep_meta)
    meta["object_cfgs"].append(cfg)
    return ET.tostring(xml, encoding="unicode"), meta, obj.root_body


def _box_obstacle(parent, dimensions, spec):
    xml = ET.fromstring(parent.xml)
    body = ET.SubElement(xml.find("worldbody"), "body", name="pick_box")
    ET.SubElement(body, "joint", name="pick_box_free", type="free", damping=str(spec["joint_damping"]))
    ET.SubElement(
        body, "geom", name="pick_box_geom", type="box", size=" ".join(map(str, dimensions / 2)),
        mass=str(spec["mass_kg"]), friction=" ".join(map(str, spec["friction"])),
        rgba=" ".join(map(str, spec["rgba"])), group="1", contype="1", conaffinity="1",
    )
    return ET.tostring(xml, encoding="unicode"), parent.ep_meta, body.attrib["name"]


def _copy_shared_state(source, dest):
    for name in source.model.joint_names:
        dest.data.set_joint_qpos(name, source.data.get_joint_qpos(name))
        dest.data.set_joint_qvel(name, source.data.get_joint_qvel(name))
    dest.data.time = source.data.time
    dest.forward()


def _pose(sim, body):
    return np.r_[sim.data.body(body).xpos, sim.data.body(body).xquat]


def _motion(before, after):
    return {"translation_m": float(np.linalg.norm(after[:3] - before[:3])),
            "rotation_rad": float(quat_angle(before[3:], after[3:]))}


def _place(sim, body, frame, offset, yaw_rad, drop_height):
    joint = sim.model.joint(int(sim.model.body(body).jntadr[0])).name
    # Use the centre of the rotated collision bounds in XY and its minimum Z.
    sim.data.set_joint_qpos(joint, np.r_[np.zeros(3), quat_yaw(yaw_rad)])
    sim.data.set_joint_qvel(joint, np.zeros(6))
    sim.forward()
    points = body_points(sim, sim.model.body(body).id)
    anchor = (points.min(axis=0) + points.max(axis=0)) / 2
    anchor[2] = points[:, 2].min()
    new_root = frame.origin + frame.axes @ [*offset, drop_height] - anchor
    sim.data.set_joint_qpos(joint, np.r_[new_root, quat_yaw(yaw_rad)])
    sim.forward()
    return joint


def _contacts(sim, body_geoms, support_names):
    model, data = sim.model._model, sim.data._data
    supporting, foreign = {}, {}
    force_n = 0.0
    for i, contact in enumerate(data.contact[:data.ncon]):
        a, b = int(contact.geom1), int(contact.geom2)
        if (a in body_geoms) == (b in body_geoms):
            continue
        other = model.geom(b if a in body_geoms else a).name
        distance = float(contact.dist)
        table = other in support_names and abs(contact.frame[2]) > 0.9
        hits = supporting if table else foreign
        hits[other] = min(distance, hits.get(other, distance))
        if table:
            wrench = np.zeros(6)
            mujoco.mj_contactForce(model, data, i, wrench)
            force_n += wrench[0]
    return {"support": supporting, "other": foreign, "support_force_n": force_n}


def _outlines(sim, geoms, frame):
    polygons = []
    for geom in geoms:
        xy = ((geom_points(sim, geom) - frame.origin) @ frame.axes)[:, :2]
        polygons.append(xy[ConvexHull(xy).vertices])
    return polygons


def _vertex_edge_distance(vertices, polygon):
    edges = np.roll(polygon, -1, axis=0) - polygon
    delta = vertices[:, None, :] - polygon[None, :, :]
    weight = np.clip((delta * edges).sum(axis=2) / (edges * edges).sum(axis=1), 0, 1)
    return np.linalg.norm(delta - weight[:, :, None] * edges, axis=2).min()


def _outline_gap(cup, obstacle):
    """Minimum horizontal separation over the projected convex collision components."""
    best = float("inf")
    for a in cup:
        for b in obstacle:
            edges = np.concatenate((np.roll(a, -1, axis=0) - a, np.roll(b, -1, axis=0) - b))
            axes = edges[:, ::-1] * [-1, 1]
            pa, pb = a @ axes.T, b @ axes.T
            separated = ((pa.max(axis=0) < pb.min(axis=0)) | (pb.max(axis=0) < pa.min(axis=0))).any()
            if not separated:
                return 0.0
            best = min(best, _vertex_edge_distance(a, b), _vertex_edge_distance(b, a))
    return best


def _settle(runtime, parent, body, frame, offset, config):
    sim, source = runtime.env.sim, parent.env.sim
    _copy_shared_state(source, sim)
    yaw = np.arctan2(frame.axes[1, 0], frame.axes[0, 0]) + np.deg2rad(config["yaw_from_counter_deg"])
    joint = _place(sim, body, frame, offset, yaw, config["drop_height_m"])
    body_id = sim.model.body(body).id
    geoms = collision_geoms(sim, body_id)
    cup_geoms = runtime.target_geoms
    cup_before, before = _pose(sim, "obj_main"), _pose(sim, body)
    initial = _contacts(sim, geoms, frame.support)
    local = (body_points(sim, body_id) - frame.origin) @ frame.axes
    cup_outline = _outlines(sim, cup_geoms, frame)
    outline = _outlines(sim, geoms, frame)
    record = {
        "proposal_pose_xyz_wxyz": before,
        "proposed_world_yaw_deg": float(np.rad2deg(yaw)),
        "initial_contacts": initial,
        "proposed_center_distance_m": float(np.linalg.norm(offset)),
        "proposed_horizontal_gap_m": _outline_gap(cup_outline, outline),
    }
    reasons = []
    if not frame.covers(local, config["edge_inset_m"]):
        reasons.append("footprint_outside_counter")
    if any(dist < -config["penetration_tolerance_m"] for dist in initial["other"].values()):
        reasons.append("initial_penetration")
    if reasons:
        return {**record, "reasons": reasons}

    runtime._sync_controllers()
    action = np.zeros(runtime.action_spec.dim)
    peak_cup_delta = 0.0
    foreign = {}
    tail = []
    start_time = sim.data.time
    for step in range(config["settle_steps"]):
        runtime.step(action)
        contact = _contacts(sim, geoms, frame.support)
        for name, dist in contact["other"].items():
            foreign[name] = min(dist, foreign.get(name, dist))
        peak_cup_delta = max(peak_cup_delta, _motion(cup_before, _pose(sim, "obj_main"))["translation_m"])
        if step >= config["settle_steps"] - config["tail_steps"]:
            tail.append(_pose(sim, body))

    after = _pose(sim, body)
    cup_motion, obstacle_motion = _motion(cup_before, _pose(sim, "obj_main")), _motion(before, after)
    settled_contacts = _contacts(sim, geoms, frame.support)
    settled_for = sim.data.time - start_time
    tail_motion = [_motion(pose, after) for pose in tail]
    velocity = sim.data.get_joint_qvel(joint).copy()
    model = sim.model._model
    mass = float(model.body(body).mass[0])
    if any(dist < -config["penetration_tolerance_m"] for dist in foreign.values()):
        reasons.append("non_support_penetration_during_settle")
    if not settled_contacts["support"] or settled_contacts["support_force_n"] < 0.5 * mass * abs(model.opt.gravity[2]):
        reasons.append("missing_load_bearing_support")
    if np.linalg.norm(after[:2] - before[:2]) > config["pose_tolerance_m"] or obstacle_motion["rotation_rad"] > config["tilt_tolerance_rad"]:
        reasons.append("obstacle_slide_or_tilt")
    if max(x["translation_m"] for x in tail_motion) > config["tail_tolerance_m"] or max(
        x["rotation_rad"] for x in tail_motion
    ) > config["tail_tolerance_rad"]:
        reasons.append("obstacle_unstable")
    if peak_cup_delta > config["pose_tolerance_m"] or cup_motion["rotation_rad"] > config["tilt_tolerance_rad"]:
        reasons.append("target_motion")

    # Keep inherited joints and velocities byte-for-byte at the parent start.
    _copy_shared_state(source, sim)
    sim.data.set_joint_qvel(joint, np.zeros(6))
    sim.forward()
    frozen_contacts = _contacts(sim, geoms, frame.support)
    local = (body_points(sim, body_id) - frame.origin) @ frame.axes
    if not frozen_contacts["support"] or not frame.covers(local, config["edge_inset_m"]):
        reasons.append("frozen_obstacle_support")
    if any(dist < -config["penetration_tolerance_m"] for dist in frozen_contacts["other"].values()):
        reasons.append("frozen_non_support_penetration")
    if set(runtime.support_contacts()) != set(parent.support_contacts()):
        reasons.append("frozen_cup_support")

    outline = _outlines(sim, geoms, frame)
    center_xy = (local.min(axis=0) + local.max(axis=0))[:2] / 2
    return {
        **record, "reasons": reasons, "settled_pose_xyz_wxyz": after, "settle_seconds": settled_for,
        "obstacle_motion": obstacle_motion, "cup_motion": cup_motion, "peak_cup_translation_m": peak_cup_delta,
        "tail_motion_max": {key: max(x[key] for x in tail_motion) for key in tail_motion[0]},
        "final_qvel": velocity, "during_settle_other_contacts": foreign, "settled_contacts": settled_contacts,
        "frozen_contacts": frozen_contacts, "actual_center_xy_m": center_xy,
        "actual_center_distance_m": float(np.linalg.norm(center_xy)),
        "horizontal_gap_m": _outline_gap(cup_outline, outline), "obstacle_outline_xy_m": outline,
    }


def build_pick_variants(parent_dir, config_path, output):
    """Make paired placement attempts for both obstacle types in the same parent kitchen."""
    config = read_config(config_path)
    parent_dir = Path(parent_dir).absolute()
    root = create_run(output, {**config, "parent_scene": str(parent_dir)})
    env_record = read_json(parent_dir / ENV_FILE)
    parent = RoboCasaRuntime(parent_dir)
    try:
        from robosuite.utils.binding_utils import MjSim

        frame = CounterFrame.from_parent(parent, config["counter"])
        kettle = _mesh_obstacle(parent, config["kettle_asset"])
        scratch = MjSim.from_xml_string(parent.env.edit_model_xml(kettle[0]))
        yaw_rad = np.arctan2(frame.axes[1, 0], frame.axes[0, 0]) + np.deg2rad(config["yaw_from_counter_deg"])
        _place(scratch, kettle[2], frame, [0, 0], yaw_rad, 0)
        kettle_points = body_points(scratch, scratch.model.body(kettle[2]).id)
        points = (kettle_points - frame.origin) @ frame.axes
        # Rotate world-space vertices back into the box's local axes to avoid doubling the yaw.
        c, s = np.cos(yaw_rad), np.sin(yaw_rad)
        box_axes = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        dimensions = np.ptp(kettle_points @ box_axes, axis=0)
        apple = parent.env.sim.model.body("distr_counter_main").id
        write_json(root / "geometry.json", {
            "origin_world": frame.origin, "axes_world": frame.axes, "support_rectangles_xy_m": frame.support,
            "cup_outline_xy_m": _outlines(parent.env.sim, parent.target_geoms, frame),
            "apple_outline_xy_m": _outlines(parent.env.sim, collision_geoms(parent.env.sim, apple), frame),
            "kettle_counter_bounds_size_m": np.ptp(points, axis=0), "box_size_m": dimensions,
        })
        manifest = root / "scenes.jsonl"
        append_row(manifest, {"id": "original", "kind": "original", "status": "accepted", "scene": str(parent_dir)})
        for kind, template in (("kettle", kettle), ("box", _box_obstacle(parent, dimensions, config["box"]))):
            xml, meta, body = template
            scratch = MjSim.from_xml_string(parent.env.edit_model_xml(xml))
            _copy_shared_state(parent.env.sim, scratch)
            _place(scratch, body, frame, config["placements"]["far"], yaw_rad, config["drop_height_m"])
            with TemporaryDirectory() as temp:
                path = write_frozen_scene(Path(temp) / "template", env_record, meta, xml, scratch.get_state().flatten())
                runtime = RoboCasaRuntime(path)
                try:
                    sim = runtime.env.sim
                    geoms = collision_geoms(sim, sim.model.body(body).id)
                    obstacle = {
                        "body": body, "collision_geoms": [sim.model.geom(g).name for g in geoms],
                        "mass_kg": float(sim.model._model.body(body).mass[0]),
                        "geom_friction": {sim.model.geom(g).name: sim.model.geom_friction[g] for g in geoms},
                        "asset": config["kettle_asset"] if kind == "kettle" else None,
                        "box_size_m": dimensions if kind == "box" else None,
                        "mjcf_box_half_size_m": dimensions / 2 if kind == "box" else None,
                        "rgba": config["box"]["rgba"] if kind == "box" else None,
                    }
                    for placement, offset in config["placements"].items():
                        variant = f"{kind}-{placement}"
                        evidence = _settle(runtime, parent, body, frame, offset, config)
                        row = {
                            "id": variant, "kind": kind, "placement": placement, "far_control": f"{kind}-far",
                            "obstacle": obstacle, "proposed_center_xy_m": offset, **evidence,
                        }
                        if evidence["reasons"]:
                            row["status"] = "rejected"
                        else:
                            scene = Path("scenes") / variant
                            write_frozen_scene(root / scene, env_record, meta, xml, sim.get_state().flatten())
                            row.update(status="accepted", scene=str(scene))
                        append_row(manifest, row)
                        print(f"{variant} {row['status']} {', '.join(evidence['reasons'])}", flush=True)
                finally:
                    runtime.close()
    finally:
        parent.close()
    return root
