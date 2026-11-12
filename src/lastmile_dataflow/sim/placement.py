import copy
import mujoco
import numpy as np
from lastmile_dataflow.runtime.control.action import torso_joints
from lastmile_dataflow.runtime.outcomes.contacts import FLOOR_PENETRATION_M, ContactSampler, simulation_failure
from lastmile_dataflow.scenes.geometry import floor_support, inside_triangles, room_triangles
from lastmile_dataflow.runtime.construction.preparation import preparation_guard
from lastmile_dataflow.scenes.geometry import body_pose, geom_points


def initialize_robot(sim, collection, *, base=None):
    if sim.started or sim.closed:
        raise RuntimeError("initialization requires a preparation session")
    rng = np.random.default_rng(collection.seed)
    initial = copy.deepcopy(sim.robot.config.initial)
    triangles = room_triangles(sim)
    if base is not None:
        candidates = [np.asarray(base, dtype=float)]
    else:
        # 从实际地板 geom 的世界范围采样，而非猜 THOR/MuJoCo 坐标变换。
        floors = [
            g
            for g in range(sim.model.ngeom)
            if "floor" in sim.model.geom(g).name.lower()
            and not sim.model.body(sim.model.geom_bodyid[g]).name.startswith("robot_0/")
            and sim.model.geom_rbound[g] > 0
        ]
        if len(triangles):
            xy_min = triangles.reshape(-1, 2).min(axis=0)
            xy_max = triangles.reshape(-1, 2).max(axis=0)
        elif not floors:
            raise ValueError("no bounded floor or room mesh; explicit --base required")
        candidates = []
        for _ in range(collection.max_initialization_trials):
            if len(triangles):
                xy = rng.uniform(xy_min, xy_max)
            else:
                g = int(rng.choice(floors))
                pos = sim.data.geom_xpos[g]
                radius = float(sim.model.geom_rbound[g])
                xy = pos[:2] + rng.uniform(-radius, radius, 2)
            candidates.append(np.r_[xy, rng.uniform(-3.0, 3.0)])
    trials = []
    for candidate in candidates:
        initial[sim.robot.initial_base_field] = candidate.tolist()
        try:
            sim.robot.initialize(initial)
        except ValueError as exc:
            trials.append({"base": candidate.tolist(), "valid": False, "reason": str(exc)})
            continue
        # 基座必须位于实际地面上方；向下 ray 排除房外/空洞。
        ground = floor_support(sim, candidate[:2])
        check = inspect_state(sim, collection, initialization=True)
        valid = bool(ground and inside_triangles(candidate[:2], triangles) and check["valid"])
        trials.append(
            {
                "base": candidate.tolist(),
                "valid": valid,
                "ground": bool(ground),
                "errors": [i for i in check["issues"] if i["severity"] == "error"],
            }
        )
        if valid:
            return {
                "status": "valid",
                "selected_base": candidate.tolist(),
                "trials": trials,
                "check": check,
                "method": "explicit" if base is not None else "bounded_floor_sampling",
            }
    return {"status": "invalid", "reason": "initialization_budget_exhausted", "trials": trials}


def inspect_state(sim, config, *, initialization=False):
    """检查当前物理状态，返回 {valid, issues, contacts, protocol}。

    valid=False 一定会让调用方（runner/execution）停止执行并给出失败结论。
    运行时接触只记录；initialization=True 才检查初态严重重叠。
    """
    m, d = sim.model, sim.data
    issues = []
    # 廉价的第一道闸：任何非有限值立即返回，避免后面算出 NaN 比较。
    if simulation_failure(sim) == "nonfinite_physics":
        issues.append({"severity": "error", "code": "nonfinite_physics"})
        return {"valid": False, "issues": issues, "contacts": []}
    # MuJoCo 自身的警告（求解器不稳、约束冲突等）一律当 error。
    for i, warning in enumerate(d.warning):
        if warning.number:
            issues.append(
                {"severity": "error", "code": "mujoco_warning", "warning_id": i, "count": int(warning.number)}
            )
    contacts = ContactSampler(sim).capture().observations()
    if initialization:
        for entry in contacts:
            scales = [float(m.geom_rbound[g]) for g in entry["geom_ids"] if m.geom_rbound[g] > 0]
            scale = min(scales) if scales else config.penetration_ceiling_m
            threshold = float(
                np.clip(scale * config.penetration_ratio, config.penetration_floor_m, config.penetration_ceiling_m)
            )
            if entry["distance_m"] < -threshold and not (
                entry["allowed_floor_base"] and entry["distance_m"] >= -FLOOR_PENETRATION_M
            ):
                issues.append(
                    {
                        "severity": "error",
                        "code": "severe_penetration",
                        **entry,
                        "initialization_threshold_m": threshold,
                    }
                )
    robot = sim.robot
    if "torso" in robot.joints.groups:
        # 躯干 6 个关节必须满足 [0,h,-2h,h,0,0] 的联动关系；偏离说明伺服跟不上或影子状态不一致。
        torso = robot.group("torso")
        if np.max(np.abs(torso - torso_joints(torso[1]))) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "torso_coupling_feedback", "actual": torso.tolist()})
        # 头部锁定目标也应保持不变（头部没有动作维度）。
        if np.max(np.abs(robot.group("head") - robot.fixed_head)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "head_feedback_drift"})
    return {"valid": not any(i["severity"] == "error" for i in issues), "issues": issues, "contacts": contacts}


def robot_contacts(sim, *, target_bodies=(), fingers=(), initialization=True):
    """Initialization overlap filter OR runtime record-only robot contacts.

    Never use the initialization filter to stop an already-running simulation.
    """
    frame = ContactSampler(sim).capture(target_bodies=target_bodies, fingers=fingers)
    contacts = [c for c in frame.observations() if c["robot_contact"]]
    if not initialization:
        return contacts
    return [
        c
        for c in contacts
        if not (c["allowed_floor_base"] and c["distance_m"] >= -FLOOR_PENETRATION_M)
        and not c["intended_target_contact"]
        and c["distance_m"] < (-0.002 if c["kind"] == "self_collision" else -0.001)
    ]


def _restore_object_velocity(sim, original_velocity):
    velocity_mask = np.ones(sim.model.nv, bool)
    robot_dofs = list(sim.robot.joints.dofs.values())
    velocity_mask[robot_dofs] = False
    sim.data.qvel[velocity_mask] = original_velocity[velocity_mask]


def place_frozen_station(sim, station):
    if sim.started or sim.closed:
        raise RuntimeError("post-begin station placement prohibited")
    old = sim.data.qpos.copy()
    old_velocity = sim.data.qvel.copy()
    sim.robot.initialize(copy.deepcopy(station["initial"]))
    # initialize() resets all qvel. A frozen station may change only the robot:
    # keep object velocities, including nonzero residual frozen motion.
    _restore_object_velocity(sim, old_velocity)
    mujoco.mj_forward(sim.model, sim.data)
    mask = np.ones(sim.model.nq, bool)
    mask[list(sim.robot.joints.addresses.values())] = False
    if not np.array_equal(old[mask], sim.data.qpos[mask]):
        raise RuntimeError("station placement changed object poses")


def park_robot(sim, anchor, config):
    """Initialize once, outside the augmentation disk. Do not alter source objects."""
    preparation_guard(sim)
    triangles = room_triangles(sim)
    if not len(triangles):
        raise ValueError("bounded room floor required for robot parking")
    points = triangles.reshape(-1, 2)
    lo, hi = points.min(axis=0), points.max(axis=0)
    rng = np.random.default_rng(config.seed)
    trials = []
    source = sim.data.qpos.copy()
    source_mask = np.ones(sim.model.nq, bool)
    source_mask[list(sim.robot.joints.addresses.values())] = False
    for i in range(256):
        xy = rng.uniform(lo, hi)
        if np.linalg.norm(xy - anchor[:2]) < config.radius_m + 1.25:
            continue
        initial = copy.deepcopy(sim.robot.config.initial)
        initial[sim.robot.initial_base_field] = [*xy, float(rng.uniform(-3.0, 3.0))]
        sim.robot.initialize(initial)
        valid = bool(floor_support(sim, xy) and not robot_contacts(sim))
        trials.append({"base": initial[sim.robot.initial_base_field], "valid": valid})
        if valid:
            if not np.array_equal(source[source_mask], sim.data.qpos[source_mask]):
                raise RuntimeError("parking changed source object state")
            return {
                "status": "valid",
                "trials": trials,
                "initial": initial,
                "source_qpos_unchanged": True,
                "scope": "pre_begin_parking_not_navigation",
            }
    raise ValueError("no_legal_robot_parking_pose")


def robot_footprint(sim, *, whole_body=False):
    """Actual base and wheel geometry projected into the base frame."""
    base = body_pose(sim, sim.model.body(sim.robot.config.namespace + sim.robot.base_body).id)
    base[:2, 3] = sim.robot.base_pose()[:2]
    points = []
    for g in range(sim.model.ngeom):
        name = sim.model.body(int(sim.model.geom_bodyid[g])).name
        if (
            whole_body
            and name.startswith(sim.robot.config.namespace)
            and (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g])
            or not whole_body
            and (
                name == sim.robot.config.namespace + sim.robot.base_body
                or name.startswith(sim.robot.config.namespace)
                and "wheel" in name.lower()
            )
        ):
            points.append((geom_points(sim, g) - base[:3, 3]) @ base[:3, :3])
    if not points:
        raise ValueError("missing chassis geometry")
    points = np.concatenate(points)
    return {
        "radius_m": float(np.linalg.norm(points[:, :2], axis=1).max()),
        "min": points.min(axis=0).tolist(),
        "max": points.max(axis=0).tolist(),
        "method": "held_whole_body_enclosing_circle"
        if whole_body
        else "base_origin_enclosing_circle_of_base_and_wheel_geoms",
    }
