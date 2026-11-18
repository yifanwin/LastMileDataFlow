"""Offline object-local grasp filtering by paired finite pad rays, followed by FPS."""

import csv
from datetime import datetime, timezone
import logging
from pathlib import Path
import time

import mujoco
import numpy as np

from lastmile_dataflow.sim.model.geometry import descendants
from lastmile_dataflow.sim.model.mjcf import load_spec
from lastmile_dataflow.sim.source import SceneSource, instance_catalog, resolve_target
from lastmile_dataflow.utils.io import create_run, write_json
from .grasp_geometry import panda_hand
from .grasps import load_grasps, select_grasps

logger = logging.getLogger(__name__)


def pad_rays(hand, resolution):
    """Measure open native pad faces and return finite segments in TCP coordinates."""
    model, data = hand.model, hand.data
    pads = [model.geom(f"robot_0/gripper0_right_finger{i}_pad_collision").id for i in (1, 2)]
    inward = data.geom_xpos[pads[1]] - data.geom_xpos[pads[0]]
    inward /= np.linalg.norm(inward)
    faces = []
    for geom, direction in zip(pads, (inward, -inward)):
        rotation = data.geom_xmat[geom].reshape(3, 3)
        axis = int(np.argmax(np.abs(rotation.T @ direction)))
        normal = rotation[:, axis] * np.sign(rotation[:, axis] @ direction)
        center = data.geom_xpos[geom] + normal * model.geom_size[geom, axis]
        tangents = [i for i in range(3) if i != axis]
        faces.append((center, normal, tangents))
    center, normal, tangents = faces[0]
    uv = np.array([(u, v) for u in np.linspace(-0.9, 0.9, resolution)
                   for v in np.linspace(-0.9, 0.9, resolution)])
    rotation = data.geom_xmat[pads[0]].reshape(3, 3)
    starts = center + (uv * model.geom_size[pads[0], tangents]) @ rotation[:, tangents].T
    other, other_normal, other_tangents = faces[1]
    distances = (other - starts) @ other_normal / (normal @ other_normal)
    ends = starts + distances[:, None] * normal
    other_local = (ends - data.geom_xpos[pads[1]]) @ data.geom_xmat[pads[1]].reshape(3, 3)
    valid = (distances > 0) & np.all(
        np.abs(other_local[:, other_tangents]) <= model.geom_size[pads[1], other_tangents] + 1e-9, axis=1
    )
    tcp_rotation = data.site_xmat[hand.site].reshape(3, 3)
    tcp_position = data.site_xpos[hand.site]
    return (starts - tcp_position) @ tcp_rotation, (ends - tcp_position) @ tcp_rotation, valid


def paired_ray_hits(model, data, meshes, starts, ends, valid):
    """Both inward first hits must bracket a positive target span inside the segment."""
    hits = np.zeros(len(starts), dtype=bool)
    for index in np.flatnonzero(valid):
        delta = ends[index] - starts[index]
        length = float(np.linalg.norm(delta))
        direction = delta / length
        left = [mujoco.mj_rayMesh(model, data, geom, starts[index], direction) for geom in meshes]
        right = [mujoco.mj_rayMesh(model, data, geom, ends[index], -direction) for geom in meshes]
        left = min((d for d in left if d >= 0), default=np.inf)
        right = min((d for d in right if d >= 0), default=np.inf)
        hits[index] = length - left - right > 1e-6
    return hits


def preprocess_grasps(scene, metadata, target, grasps, output, *, robot_config, robot_scale,
                      roll_rad, resolution, min_hit_ratio, fps_count, fps_seed,
                      position_scale_m, rotation_scale_rad):
    """Write an object-local library; the input scene only supplies object geometry."""
    if resolution < 2 or not 0 <= min_hit_ratio <= 1:
        raise ValueError("Require resolution >= 2 and min_hit_ratio in [0, 1]")
    started = time.monotonic()
    scene, metadata, grasps, robot_config = [str(Path(p).absolute()) for p in (scene, metadata, grasps, robot_config)]
    config = dict(scene=scene, metadata=metadata, target=target, grasps=grasps,
                  robot_config=robot_config, robot_scale=robot_scale, roll_rad=roll_rad,
                  resolution=resolution, min_hit_ratio=min_hit_ratio, fps_count=fps_count,
                  fps_seed=fps_seed, position_scale_m=position_scale_m, rotation_scale_rad=rotation_scale_rad)
    output = create_run(output, config)
    model = load_spec(scene).compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    source = SceneSource("grasp-preprocessing", scene, metadata)
    body = resolve_target(model, instance_catalog(model, source), target)
    bodies = descendants(model, body)
    meshes = [g for g in range(model.ngeom) if model.geom_bodyid[g] in bodies
              and model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH
              and not (model.geom_contype[g] or model.geom_conaffinity[g])]
    if not meshes:
        raise ValueError("Target has no visual mesh")
    hand = panda_hand(robot_config, robot_scale)
    starts, ends, valid = pad_rays(hand, resolution)
    candidates = load_grasps(grasps, model.body(body).name)
    object_world = np.eye(4)
    object_world[:3, :3], object_world[:3, 3] = data.xmat[body].reshape(3, 3), data.xpos[body]
    roll = np.eye(4)
    roll[:3, :3] = [[np.cos(roll_rad), -np.sin(roll_rad), 0],
                   [np.sin(roll_rad), np.cos(roll_rad), 0], [0, 0, 1]]
    required = int(np.ceil(min_hit_ratio * len(starts)))
    accepted, measurements = [], []
    for index, candidate in enumerate(candidates):
        goal = object_world @ candidate["pose_local"] @ roll
        world_starts = starts @ goal[:3, :3].T + goal[:3, 3]
        world_ends = ends @ goal[:3, :3].T + goal[:3, 3]
        count = int(paired_ray_hits(model, data, meshes, world_starts, world_ends, valid).sum())
        keep = count >= required
        measurements.append(dict(source_row=candidate["row"], paired_hits=count,
                                 rays=len(starts), hit_ratio=count / len(starts), accepted=keep))
        if keep:
            accepted.append(candidate)
        if (index + 1) % 100 == 0:
            logger.info("Checked %d/%d grasps, accepted %d", index + 1, len(candidates), len(accepted))
    pool = select_grasps(accepted, [c["pose_local"] for c in accepted], fps_count,
                         position_scale_m, rotation_scale_rad, seed=fps_seed)
    np.savez(output / "grasps.npz", transforms=np.array([c["pose_local"] for c in pool]).reshape(-1, 4, 4),
             source_rows=np.array([c["row"] for c in pool], dtype=int), source=str(Path(grasps).resolve()),
             robot_scale=robot_scale, roll_rad=roll_rad, resolution=resolution,
             min_hit_ratio=min_hit_ratio, fps_count=fps_count, fps_seed=fps_seed)
    with (output / "checks.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("source_row", "paired_hits", "rays", "hit_ratio", "accepted"))
        writer.writeheader()
        writer.writerows(measurements)
    summary = dict(time=datetime.now(timezone.utc).isoformat(), valid_count=len(candidates),
                   accepted_count=len(accepted), pool_count=len(pool), required_hits=required,
                   source_rows=[c["row"] for c in pool], target_body=model.body(body).name,
                   frame="target_body_local", transforms_include_roll=False,
                   pad_endpoint_valid_count=int(valid.sum()), elapsed_s=time.monotonic() - started,
                   scope="target visual mesh only; no scene obstacles, reachability or dynamics")
    write_json(output / "summary.json", summary)
    return summary
