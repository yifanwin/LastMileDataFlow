"""Baselines B0/B1/B2 for station quality (Vahrenkamp et al., ICRA 2013, "Robot placement based on reachability inversion").

RD: uniform joint-space samples of the 7-DoF arm (the torso is locked at 0.00 m, as in every evaluator),
FK of the TCP in the planning base frame, binned into a dense 6D grid (x, y, z, roll, pitch, yaw) keeping the
voxel maximum of the extended manipulability  e = w * P_L * P_D :
  w   = sqrt(det(J J^T))                                   Yoshikawa manipulability
  P_L = prod_i 1 - exp(-K_L (q_i - l_i)(u_i - q_i) / (u_i - l_i)^2)   joint-limit penalty
  P_D = 1 - exp(-d_self / D_SELF_M)                        self-distance penalty; self-colliding samples are dropped
normalized by the global maximum. Candidate stations are given, so the ORM is queried lazily: the grasp pose in
the station's base frame (q_b^-1 p_grasp, exported per station) is looked up in the RD, equivalent to the IRD
cut by the floor plane.
B1 = mean (and max) of ORM over the grasp pool.
B2 = ORM_tr = min over approach/grasp/lift waypoints (paper eq. 3), then Algorithm 1: grasps with ORM_tr > 0
     need a collision-free IK (self + static scene) at every waypoint, otherwise they score 0.
B0 = Gaussian in base-to-target distance around the native spawn distance and in heading error to the target.
"""
import time
from dataclasses import dataclass

import numpy as np
import torch

POS_RES_M = 0.05
ANG_BINS = (12, 6, 12)  # roll, pitch, yaw: 30 deg
BOUNDS_M = ((-1.0, 1.4), (-1.2, 1.2), (-0.2, 2.0))
K_L = 50.0
D_SELF_M = 0.05
APPROACH_M = 0.08
LIFT_M = 0.10
B0_SIGMA_D_M = 0.15
B0_SIGMA_YAW = np.radians(30.0)
WAYPOINTS = ("approach", "grasp", "lift")


def b0_score(base_xy, base_yaw, target_xy, ideal_distance_m):
    d = np.hypot(*(np.asarray(target_xy) - base_xy))
    bearing = np.arctan2(target_xy[1] - base_xy[1], target_xy[0] - base_xy[0])
    err = (bearing - base_yaw + np.pi) % (2 * np.pi) - np.pi
    return float(np.exp(-0.5 * ((d - ideal_distance_m) / B0_SIGMA_D_M) ** 2) * np.exp(-0.5 * (err / B0_SIGMA_YAW) ** 2))


def waypoints(grasp_base):
    """Approach (back along tool z), grasp, lift (+z in the base frame, which is level with the world)."""
    approach = grasp_base.copy(); approach[:3, 3] -= APPROACH_M * grasp_base[:3, 2]
    lift = grasp_base.copy(); lift[2, 3] += LIFT_M
    return {"approach": approach, "grasp": grasp_base, "lift": lift}


def _rpy(R):
    pitch = torch.asin(torch.clamp(-R[..., 2, 0], -1, 1))
    roll = torch.atan2(R[..., 2, 1], R[..., 2, 2])
    yaw = torch.atan2(R[..., 1, 0], R[..., 0, 0])
    return roll, pitch, yaw


class ReachabilityGrid:
    def __init__(self, device="cuda"):
        lo = torch.tensor([b[0] for b in BOUNDS_M], device=device)
        self.lo = lo
        self.shape = tuple(int(np.ceil((b[1] - b[0]) / POS_RES_M)) for b in BOUNDS_M) + ANG_BINS
        self.values = torch.zeros(int(np.prod(self.shape)), device=device)
        self.device = device

    def keys(self, position, R):
        """Flat voxel index per pose; -1 outside the grid."""
        p = torch.floor((position - self.lo) / POS_RES_M).long()
        roll, pitch, yaw = _rpy(R)
        a = torch.stack([
            torch.floor((roll + np.pi) / (2 * np.pi) * ANG_BINS[0]).long() % ANG_BINS[0],
            torch.clamp(torch.floor((pitch + np.pi / 2) / np.pi * ANG_BINS[1]).long(), 0, ANG_BINS[1] - 1),
            torch.floor((yaw + np.pi) / (2 * np.pi) * ANG_BINS[2]).long() % ANG_BINS[2],
        ], -1)
        idx = torch.cat([p, a], -1)
        dims = torch.tensor(self.shape, device=self.device)
        inside = ((idx >= 0) & (idx < dims)).all(-1)
        stride = torch.tensor(np.r_[np.cumprod(self.shape[::-1])[::-1][1:], 1], device=self.device)
        return torch.where(inside, (idx * stride).sum(-1), torch.full_like(idx[:, 0], -1))

    def add(self, keys, value):
        ok = keys >= 0
        self.values.scatter_reduce_(0, keys[ok], value[ok], reduce="amax")

    def query(self, poses):
        """RD value of 4x4 poses in the base frame (0 for empty or outside voxels)."""
        T = torch.as_tensor(np.asarray(poses), dtype=torch.float32, device=self.device).reshape(-1, 4, 4)
        k = self.keys(T[:, :3, 3], T[:, :3, :3])
        out = torch.zeros(len(k), device=self.device)
        out[k >= 0] = self.values[k[k >= 0]]
        return out.cpu().numpy()

    def save(self, path, meta):
        nz = torch.nonzero(self.values).squeeze(1)
        np.savez_compressed(path, index=nz.cpu().numpy().astype(np.int64), value=self.values[nz].cpu().numpy().astype(np.float32),
                            shape=np.array(self.shape), meta=np.array(str(meta)))

    @classmethod
    def load(cls, path, device="cuda"):
        grid = cls(device)
        with np.load(path) as f:
            if tuple(f["shape"]) != grid.shape:
                raise ValueError("RD grid shape differs from the current resolution")
            grid.values[torch.as_tensor(f["index"], device=device)] = torch.as_tensor(f["value"], device=device)
        return grid


def _self_distance(spheres, pairs, padding, chunk=8192):
    """Minimum signed sphere-pair distance (negative = self collision) per sample."""
    out = []
    for s in torch.split(spheres, chunk):
        a, b = s[:, pairs[:, 0]], s[:, pairs[:, 1]]
        d = torch.linalg.norm(a[..., :3] - b[..., :3], dim=-1) - a[..., 3] - b[..., 3] - padding[pairs[:, 0]] - padding[pairs[:, 1]]
        valid = (a[..., 3] > 0) & (b[..., 3] > 0)
        out.append(torch.where(valid, d, torch.full_like(d, 1e3)).amin(-1))
    return torch.cat(out)


def build_rd(kinematics, joint_names, samples, batch=200_000, seed=0, log=print):
    from curobo.types import JointState

    torch.manual_seed(seed)
    kinematics.compute_jacobian = True  # off by default: tool_jacobians come back as zeros
    limits = kinematics.get_joint_limits().position
    lo, hi = limits[0], limits[1]
    sc = kinematics.get_self_collision_config()
    pairs, padding = sc.collision_pairs.long(), sc.sphere_padding
    grid = ReachabilityGrid()
    raw = torch.zeros(int(np.prod(grid.shape)), device=grid.device)
    stats = {"samples": 0, "self_collision": 0, "outside_grid": 0, "filled_at": {}}
    checkpoints = {int(samples * f) for f in (0.125, 0.25, 0.5, 1.0)}
    started = time.monotonic()
    while stats["samples"] < samples:
        q = lo + (hi - lo) * torch.rand(batch, len(lo), device=grid.device)
        s = kinematics.compute_kinematics(JointState.from_position(q, joint_names=joint_names))
        pos = s.tool_poses.position.reshape(batch, 3)
        R = quat_to_matrix(s.tool_poses.quaternion.reshape(batch, 4))
        J = s.tool_jacobians.reshape(batch, 6, len(lo))
        w = torch.sqrt(torch.clamp(torch.linalg.det(J @ J.transpose(1, 2)), min=0))
        span = (hi - lo) ** 2
        p_l = torch.prod(1 - torch.exp(-K_L * (q - lo) * (hi - q) / span), dim=1)
        d_self = _self_distance(s.robot_spheres.reshape(batch, -1, 4), pairs, padding)
        p_d = 1 - torch.exp(-torch.clamp(d_self, min=0) / D_SELF_M)
        e = torch.where(d_self > 0, w * p_l * p_d, torch.zeros_like(w))
        keys = grid.keys(pos, R)
        stats["self_collision"] += int((d_self <= 0).sum()); stats["outside_grid"] += int((keys < 0).sum())
        grid.add(keys, e)
        stats["samples"] += batch
        for c in sorted(checkpoints):
            if stats["samples"] >= c and str(c) not in stats["filled_at"]:
                stats["filled_at"][str(c)] = int((grid.values > 0).sum())
                log(f"rd samples {stats['samples']} filled {stats['filled_at'][str(c)]} {time.monotonic() - started:.0f}s")
    peak = grid.values.max()
    grid.values /= peak
    stats.update(raw_max=float(peak), voxels=int(np.prod(grid.shape)), filled=int((grid.values > 0).sum()), build_s=time.monotonic() - started)
    return grid, stats


def quat_to_matrix(q):
    w, x, y, z = q.unbind(-1)
    return torch.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
    ], -1).reshape(*q.shape[:-1], 3, 3)


@dataclass
class StationScore:
    b1_mean: float
    b1_max: float
    b2_mean: float
    b2_max: float
    b1_s: float
    b2_s: float
    grasps: list  # per-grasp ORM per waypoint, ORM_tr, B2 verdict and trace


def score_station(grid, planner, problem, *, trace_planner=None):
    """B1 and B2 for one exported (scene, station) problem; ``planner`` has the station's world loaded."""
    t0 = time.monotonic()
    goals = problem["goals_base"]
    wps = [waypoints(T) for T in goals]
    orm = {w: grid.query([p[w] for p in wps]) for w in WAYPOINTS}
    b1 = orm["grasp"]
    t1 = time.monotonic()
    orm_tr = np.minimum.reduce([orm[w] for w in WAYPOINTS])
    rows, b2 = [], np.zeros(len(goals))
    for g in range(len(goals)):
        row = {"grasp": g, "orm": {w: float(orm[w][g]) for w in WAYPOINTS}, "orm_tr": float(orm_tr[g])}
        if orm_tr[g] <= 0:
            row["verdict"] = "orm_zero"
        else:
            failed = next((w for w in WAYPOINTS if not planner.ik_ok(wps[g][w], problem["arm"])), None)
            if failed is None:
                b2[g], row["verdict"] = orm_tr[g], "pass"
            else:
                row.update(verdict="ik_or_collision", waypoint=failed)
                if trace_planner is not None:
                    row["trace"] = trace_planner.trace(wps[g][failed], problem)
        rows.append(row)
    t2 = time.monotonic()
    return StationScore(float(b1.mean()), float(b1.max()), float(b2.mean()), float(b2.max()), t1 - t0, t2 - t0, rows)


class IKChecker:
    """Single-pose IK on a RoboCasaGraspPlanner: with its loaded world (B2 filter) or an empty one (trace)."""

    def __init__(self, planner):
        self.p = planner
        kc = planner.mp.kinematics.config.kinematics_config
        names = {i: n for n, i in kc.link_name_to_idx_map.items()}
        self.sphere_link = [names.get(int(i), str(int(i))) for i in kc.link_sphere_idx_map.cpu().numpy()]

    def solve(self, pose, arm):
        from curobo.types import JointState
        from lastmile_dataflow.planning.robocasa_curobo import ARM_JOINTS

        st = JointState.from_position(self.p.dev.to_device(np.asarray(arm, dtype=np.float32)).view(1, -1), joint_names=ARM_JOINTS)
        r = self.p.mp.ik_solver.solve_pose(self.p._goals([pose]), return_seeds=1, current_state=st)
        return bool(r.success.any().item()), r.solution.reshape(-1, 7)[:1]

    def ik_ok(self, pose, arm):
        return self.solve(pose, arm)[0]

    def trace(self, pose, problem, top=4):
        """Collision-free-world IK, then robot sphere vs exported geom AABB penetration (approximate pairs)."""
        from curobo.types import JointState
        from lastmile_dataflow.planning.robocasa_curobo import ARM_JOINTS

        ok, q = self.solve(pose, problem["arm"])
        if not ok:
            return {"free_ik": False}
        s = self.p.mp.kinematics.compute_kinematics(JointState.from_position(q, joint_names=ARM_JOINTS))
        sph = s.robot_spheres.reshape(-1, 4).cpu().numpy()
        counts = problem["geom_vertex_counts"]
        ends = np.cumsum(counts)
        V = problem["vertices"]
        hits = []
        for g, (a, b) in enumerate(zip(ends - counts, ends)):
            lo, hi = V[a:b].min(0), V[a:b].max(0)
            d = np.linalg.norm(np.maximum(np.maximum(lo - sph[:, :3], 0), sph[:, :3] - hi), axis=1) - sph[:, 3]
            d[sph[:, 3] <= 0] = np.inf
            for i in np.flatnonzero(d < 0):
                hits.append((float(d[i]), self.sphere_link[i], str(problem["geom_names"][g])))
        pairs = {}
        for depth, link, geom in sorted(hits):
            pairs.setdefault((link, geom), depth)
        return {"free_ik": True, "pairs": [{"link": l, "geom": g, "depth_m": round(-d, 4)} for (l, g), d in list(pairs.items())[:top]]}
