"""GraspDataGen-style grasp candidates for a scene target, checked in MuJoCo.

Finger-pad placement after GraspDataGen ``grasp_guess``: sample a point and outward normal on the target's
collision surface, close the open hand along that normal, spin the approach about it and centre the pads on
the object. The fully open native hand must clear the target and the fixed scene at the grasp and along a
retreat corridor; the corridor length is recorded rather than imposed.
"""

from dataclasses import dataclass
import logging
import time
from types import SimpleNamespace

import mujoco
import numpy as np

from lastmile_dataflow.robots.registry import load_robot_config, load_robot_spec
from lastmile_dataflow.sim.model.geometry import body_points, descendants
from lastmile_dataflow.sim.model.mjcf import load_spec
from lastmile_dataflow.sim.source import SceneSource, instance_catalog, resolve_target
from .grasp_geometry import panda_hand
from .grasp_preprocessing import object_symmetry, pad_rays

logger = logging.getLogger(__name__)

HAND = "grasp_hand"
MOCAP = "grasp_hand_target"
PREFIX = "hand/"
WRIST = "robot_0/robot0_link7"
WRIST_JOINT = "robot_0/robot0_joint7"
PADS = tuple(f"{PREFIX}robot_0/gripper0_right_finger{i}_pad_collision" for i in (1, 2))
FINGERS = tuple(f"{PREFIX}robot_0/gripper0_right_finger_joint{i}" for i in (1, 2))
RAY_GROUP = 5


@dataclass
class GraspScene:
    """Fixed scene + target + native Panda hand and link 7 on a free joint whose frame is the TCP, welded to a mocap."""
    model: mujoco.MjModel
    data: mujoco.MjData
    target: int
    target_geoms: np.ndarray
    hand_geoms: np.ndarray
    pad_geoms: np.ndarray
    mocap: int
    hand_qpos: int             # free-joint qpos address of the hand body
    finger_qpos: np.ndarray
    finger_open: np.ndarray
    ray_starts: np.ndarray     # pad-1 face points, TCP frame
    ray_ends: np.ndarray       # matching points on the pad-2 face
    object_world: np.ndarray
    table_z: float


@dataclass
class GraspCandidate:
    pose_world: np.ndarray
    point: np.ndarray
    normal: np.ndarray
    hit_ratio: float
    approach_clear_m: float


def matrix_quat(rotation):
    quat = np.empty(4)
    mujoco.mju_mat2Quat(quat, np.ascontiguousarray(rotation).ravel())
    return quat


def build_scene(scene, metadata, target, robot_config, *, resolution=5):
    """Drop movable bodies other than the target (variant-specific), then add the open hand welded to a mocap."""
    hand = panda_hand(robot_config, 1.0)
    starts, ends, valid = pad_rays(hand, resolution)
    spec = load_spec(scene)
    model = spec.compile()
    body = resolve_target(model, instance_catalog(model, SceneSource("grasp-generation", str(scene), str(metadata))),
                          target)
    name = model.body(body).name
    for joint in range(model.njnt):
        owner = model.body(int(model.jnt_bodyid[joint])).name
        if model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_FREE and owner != name:
            spec.delete(spec.body(owner))
    robot = load_robot_spec(load_robot_config(robot_config), scale=1.0)
    # Carry link 7 with the hand: it is rigid with the flange and is what meets the table on low side grasps.
    hm, hd = hand.model, hand.data
    tcp_world, wrist_world = np.eye(4), np.eye(4)
    tcp_world[:3, :3], tcp_world[:3, 3] = hd.site_xmat[hand.site].reshape(3, 3), hd.site_xpos[hand.site]
    wrist = hm.body(WRIST).id
    wrist_world[:3, :3], wrist_world[:3, 3] = hd.xmat[wrist].reshape(3, 3), hd.xpos[wrist]
    tcp_to_wrist = np.linalg.inv(tcp_world) @ wrist_world
    # The hand is a free body welded to a mocap target so contacts see its real velocity.
    spec.worldbody.add_body(name=MOCAP, mocap=True)
    body = spec.worldbody.add_body(name=HAND, gravcomp=1.0)
    body.add_freejoint()
    root = body.add_frame(pos=tcp_to_wrist[:3, 3], quat=matrix_quat(tcp_to_wrist[:3, :3])).attach_body(
        robot.body(WRIST), PREFIX, "")
    root.pos, root.quat = [0, 0, 0], [1, 0, 0, 0]  # attach keeps the arm-relative pose; the frame alone places it
    for actuator in list(spec.actuators):
        if actuator.target == PREFIX + WRIST_JOINT:
            spec.delete(actuator)
    spec.delete(spec.joint(PREFIX + WRIST_JOINT))
    for child in [root, *root.find_all(mujoco.mjtObj.mjOBJ_BODY)]:
        child.gravcomp = 1.0
    spec.add_equality(type=mujoco.mjtEq.mjEQ_WELD, objtype=mujoco.mjtObj.mjOBJ_BODY, name1=MOCAP, name2=HAND,
                      solref=[0.01, 1.0])
    model = spec.compile()
    data = mujoco.MjData(model)
    target, hand_body = model.body(name).id, model.body(HAND).id
    collides = (model.geom_contype != 0) | (model.geom_conaffinity != 0)
    target_bodies, hand_bodies = descendants(model, target), descendants(model, hand_body)
    target_geoms = np.flatnonzero(collides & np.isin(model.geom_bodyid, list(target_bodies)))
    hand_geoms = np.flatnonzero(collides & np.isin(model.geom_bodyid, list(hand_bodies)))
    model.geom_group[model.geom_group == RAY_GROUP] = 0
    model.geom_group[target_geoms] = RAY_GROUP
    finger_qpos = np.array([model.jnt_qposadr[model.joint(j).id] for j in FINGERS])
    finger_open = np.array([model.jnt_range[model.joint(FINGERS[0]).id, 1], model.jnt_range[model.joint(FINGERS[1]).id, 0]])
    data.qpos[finger_qpos] = finger_open
    mujoco.mj_forward(model, data)
    site = model.site(f"{PREFIX}robot_0/gripper0_right_grip_site").id
    if not (np.allclose(data.site_xpos[site], 0, atol=1e-6) and np.allclose(data.site_xmat[site], np.eye(3).ravel(), atol=1e-6)):
        raise ValueError("attached hand TCP does not coincide with its mocap frame")
    object_world = np.eye(4)
    object_world[:3, :3], object_world[:3, 3] = data.xmat[target].reshape(3, 3), data.xpos[target]
    table_z = float(body_points(SimpleNamespace(model=model, data=data), target)[:, 2].min())
    return GraspScene(model, data, target, target_geoms, hand_geoms,
                      np.array([model.geom(p).id for p in PADS]), int(model.body_mocapid[model.body(MOCAP).id]),
                      int(model.jnt_qposadr[model.body_jntadr[hand_body]]), finger_qpos, finger_open, starts[valid], ends[valid], object_world, table_z)


def surface_samples(scene, count, rng):
    """Area-weighted points with outward normals on the target collision meshes (world frame)."""
    model, data = scene.model, scene.data
    triangles = []
    for g in scene.target_geoms:
        if model.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            raise ValueError(f"unsupported target collision geom: {model.geom(g).name}")
        mesh = int(model.geom_dataid[g])
        vert = model.mesh_vert[model.mesh_vertadr[mesh]:model.mesh_vertadr[mesh] + model.mesh_vertnum[mesh]]
        face = model.mesh_face[model.mesh_faceadr[mesh]:model.mesh_faceadr[mesh] + model.mesh_facenum[mesh]]
        world = vert @ data.geom_xmat[g].reshape(3, 3).T + data.geom_xpos[g]
        tri = world[face]
        normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        # Collision pieces are convex: orient every face away from its piece centre.
        flip = np.einsum("ij,ij->i", normal, tri.mean(1) - world.mean(0)) < 0
        tri[flip] = tri[flip][:, ::-1]
        triangles.append(tri)
    tri = np.concatenate(triangles)
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(normal, axis=1)
    keep = area > 1e-12
    tri, normal, area = tri[keep], normal[keep] / area[keep, None], area[keep]
    index = rng.choice(len(tri), count, p=area / area.sum())
    u, v = rng.random((2, count))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = tri[index]
    points = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
    return points, normal[index]


def placements(scene, points, normals, orientations, standoff, rng):
    """TCP poses closing along -normal with pad 1 `standoff` outside the point, approach spun about the normal."""
    face = scene.ray_starts.mean(0)
    closing = (scene.ray_ends - scene.ray_starts).mean(0)
    closing /= np.linalg.norm(closing)
    approach = np.array([0.0, 0.0, 1.0])
    local = np.stack([closing, np.cross(approach, closing), approach], axis=1)
    poses = []
    for point, normal in zip(points, normals):
        u = np.cross(normal, [1.0, 0, 0] if abs(normal[0]) < 0.9 else [0, 1.0, 0])
        u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        for angle in rng.random() * 2 * np.pi + np.arange(orientations) * 2 * np.pi / orientations:
            z = np.cos(angle) * u + np.sin(angle) * v
            world = np.stack([-normal, np.cross(z, -normal), z], axis=1)
            pose = np.eye(4)
            pose[:3, :3] = world @ local.T
            pose[:3, 3] = point + standoff * normal - pose[:3, :3] @ face
            poses.append(pose)
    return np.array(poses)


def pad_gaps(scene, pose):
    """Per pad ray: distance from each pad face to the target, and the face-to-face length."""
    model, data = scene.model, scene.data
    starts = scene.ray_starts @ pose[:3, :3].T + pose[:3, 3]
    ends = scene.ray_ends @ pose[:3, :3].T + pose[:3, 3]
    direction = ends[0] - starts[0]
    length = float(np.linalg.norm(direction))
    direction /= length
    group = np.zeros(6, dtype=np.uint8)
    group[RAY_GROUP] = 1
    geom = np.zeros(1, dtype=np.int32)
    a = np.array([mujoco.mj_ray(model, data, s, direction, group, 1, -1, geom) for s in starts])
    b = np.array([mujoco.mj_ray(model, data, e, -direction, group, 1, -1, geom) for e in ends])
    hit = (a >= 0) & (b >= 0) & (a + b < length)
    return a, b, hit, direction


def centre(scene, pose):
    """Shift along the closing axis so both pads have the same smallest gap (GraspDataGen centring)."""
    a, b, hit, direction = pad_gaps(scene, pose)
    if not hit.any():
        return None
    shifted = pose.copy()
    shifted[:3, 3] += (a[hit].min() - b[hit].min()) / 2 * direction
    return shifted


def set_hand(scene, pose):
    """Place both the mocap target and the welded hand body at a TCP pose."""
    quat = matrix_quat(pose[:3, :3])
    scene.data.mocap_pos[scene.mocap], scene.data.mocap_quat[scene.mocap] = pose[:3, 3], quat
    scene.data.qpos[scene.hand_qpos:scene.hand_qpos + 7] = np.r_[pose[:3, 3], quat]


def hand_clear(scene, pose):
    """No hand collision geom within the model's hand margin of any non-hand geom."""
    set_hand(scene, pose)
    mujoco.mj_kinematics(scene.model, scene.data)
    mujoco.mj_collision(scene.model, scene.data)
    contact = scene.data.contact
    hand = np.isin(contact.geom1, scene.hand_geoms) != np.isin(contact.geom2, scene.hand_geoms)
    return not hand.any()


def approach_clearance(scene, pose, max_m, step_m):
    """Largest retreat along -approach whose every sampled hand pose is clear (0 if only the grasp is)."""
    clear = 0.0
    for distance in np.arange(1, int(round(max_m / step_m)) + 1) * step_m:
        retreat = pose.copy()
        retreat[:3, 3] -= distance * pose[:3, 2]
        if not hand_clear(scene, retreat):
            break
        clear = float(distance)
    return clear


def generate_candidates(scene, *, points, orientations, standoff_m, min_hit_ratio, clearance_m,
                        corridor_max_m, corridor_step_m, seed):
    """Return accepted candidates and the per-stage counts."""
    started = time.monotonic()
    rng = np.random.default_rng(seed)
    model = scene.model
    model.geom_margin[scene.hand_geoms] = clearance_m
    surface, normals = surface_samples(scene, points, rng)
    poses = placements(scene, surface, normals, orientations, standoff_m, rng)
    counts = dict(placed=len(poses), centred=0, pad_overlap=0, clear=0)
    accepted = []
    for index, pose in enumerate(poses):
        pose = centre(scene, pose)
        if pose is None:
            continue
        counts["centred"] += 1
        hit = pad_gaps(scene, pose)[2]
        ratio = float(hit.mean())
        if ratio < min_hit_ratio:
            continue
        counts["pad_overlap"] += 1
        if not hand_clear(scene, pose):
            continue
        counts["clear"] += 1
        point = index // orientations
        accepted.append(GraspCandidate(pose, surface[point], normals[point], ratio,
                                       approach_clearance(scene, pose, corridor_max_m, corridor_step_m)))
        if (index + 1) % 1000 == 0:
            logger.info("Checked %d/%d placements, %d clear", index + 1, len(poses), len(accepted))
    model.geom_margin[scene.hand_geoms] = 0.0
    counts["elapsed_s"] = time.monotonic() - started
    return accepted, counts


def target_symmetry(scene, tolerance_m):
    """Object-local vertical symmetry axis and centre as stored by the preprocessing library."""
    model, data, target = scene.model, scene.data, scene.target
    points = body_points(SimpleNamespace(model=model, data=data), target)
    symmetric, centre_world, deviation = object_symmetry(model, data, list(scene.target_geoms), points,
                                                         tolerance_m=tolerance_m)
    rotation = data.xmat[target].reshape(3, 3)
    centre_world[2] = data.xpos[target][2]
    return dict(axisymmetric=bool(symmetric), symmetry_axis_local=rotation.T @ [0, 0, 1.0],
                symmetry_center_local=rotation.T @ (centre_world - data.xpos[target]),
                symmetry_deviation_m=float(deviation))


@dataclass(frozen=True)
class PickSettings:
    """Mocap pick episode; judged like native PickJudge every control step."""
    approach_max_m: float = 0.05
    approach_speed_mps: float = 0.1
    close_s: float = 1.0
    lift_m: float = 0.10
    lift_s: float = 1.0
    hold_s: float = 1.0
    control_dt: float = 0.05
    min_bottom_lift_m: float = 0.02
    required_hold_s: float = 0.5


def judge(scene):
    """(both pads touch the target, target touches only the hand, target bottom height above the table)."""
    model, data = scene.model, scene.data
    contact = data.contact
    g1, g2 = contact.geom1, contact.geom2
    target1, target2 = np.isin(g1, scene.target_geoms), np.isin(g2, scene.target_geoms)
    pads = all(((g1 == p) & target2).any() or ((g2 == p) & target1).any() for p in scene.pad_geoms)
    other1, other2 = ~np.isin(g1, scene.hand_geoms) & ~target1, ~np.isin(g2, scene.hand_geoms) & ~target2
    environment = ((target1 & other2) | (target2 & other1)).any()
    bottom = body_points(SimpleNamespace(model=model, data=data), scene.target)[:, 2].min() - scene.table_z
    return pads, not environment, float(bottom)


def simulate_pick(scene, pose, approach_m, settings, initial_qpos):
    """Approach along +z from `approach_m` back, close, lift straight up, hold; returns the outcome record."""
    model, data = scene.model, scene.data
    mujoco.mj_resetData(model, data)
    data.qpos[:] = initial_qpos
    data.ctrl[:] = scene.finger_open
    start = pose.copy()
    start[:3, 3] -= approach_m * pose[:3, 2]
    set_hand(scene, start)
    mujoco.mj_forward(model, data)
    dt = model.opt.timestep
    per_control = max(1, int(round(settings.control_dt / dt)))
    approach_steps = int(np.ceil(approach_m / settings.approach_speed_mps / dt))
    for step in range(1, approach_steps + 1):
        data.mocap_pos[scene.mocap] = start[:3, 3] + (pose[:3, 3] - start[:3, 3]) * step / approach_steps
        mujoco.mj_step(model, data)
    data.ctrl[:] = 0.0
    phases = [(settings.close_s, 0.0), (settings.lift_s, settings.lift_m), (settings.hold_s, 0.0)]
    position, held, best_held, peak, pads_first = pose[:3, 3].copy(), 0.0, 0.0, -np.inf, None
    elapsed = 0.0
    for duration, rise in phases:
        controls = int(round(duration / settings.control_dt))
        for _ in range(controls):
            for _ in range(per_control):
                position[2] += rise / (controls * per_control)
                data.mocap_pos[scene.mocap] = position
                mujoco.mj_step(model, data)
            elapsed += settings.control_dt
            pads, free, bottom = judge(scene)
            peak = max(peak, bottom)
            if pads and pads_first is None:
                pads_first = elapsed
            held = held + settings.control_dt if pads and free and bottom >= settings.min_bottom_lift_m else 0.0
            best_held = max(best_held, held)
    return dict(success=bool(best_held >= settings.required_hold_s - 1e-9), held_s=best_held,
                peak_bottom_m=peak, pads_first_s=pads_first, final_pads=bool(pads))


def perturb(pose, rng, position_m, angle_rad):
    """Uniform offset in a ball and a rotation by `angle_rad` about a random axis, applied about the TCP."""
    direction = rng.normal(size=3)
    offset = direction / np.linalg.norm(direction) * position_m * rng.random() ** (1 / 3)
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    quat = np.empty(4)
    mujoco.mju_axisAngle2Quat(quat, axis, angle_rad * rng.uniform(-1, 1))
    rotation = np.empty(9)
    mujoco.mju_quat2Mat(rotation, quat)
    result = pose.copy()
    result[:3, :3] = rotation.reshape(3, 3) @ pose[:3, :3]
    result[:3, 3] += offset
    return result


def axis_rotation(axis, centre, angle):
    """Object-local 4x4 rotation by `angle` about the line through `centre` along `axis`."""
    quat = np.empty(4)
    mujoco.mju_axisAngle2Quat(quat, axis, angle)
    rotation = np.empty(9)
    mujoco.mju_quat2Mat(rotation, quat)
    result = np.eye(4)
    result[:3, :3] = rotation.reshape(3, 3)
    result[:3, 3] = centre - result[:3, :3] @ centre
    return result


def grasp_azimuth(pose, axis, centre):
    """Azimuth of the TCP about the axis; the reversed approach for TCPs within 5 mm of the axis."""
    u = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    radial = pose[:3, 3] - centre
    radial -= (radial @ axis) * axis
    if np.linalg.norm(radial) < 0.005:
        radial = -pose[:3, 2]
    return float(np.arctan2(radial @ v, radial @ u))


def canonical_grasp(pose, axis, centre):
    """Rotate about the symmetry axis to azimuth 0 (if given) and pick the finger-swap twin with x . axis >= 0."""
    if axis is not None:
        pose = axis_rotation(axis, centre, -grasp_azimuth(pose, axis, centre)) @ pose
    up = axis if axis is not None else np.array([0, 0, 1.0])
    if pose[:3, 0] @ up < 0:
        pose = pose @ np.diag([-1.0, -1.0, 1.0, 1.0])
    return pose
