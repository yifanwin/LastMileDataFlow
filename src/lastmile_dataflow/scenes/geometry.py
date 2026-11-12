"""Conservative collision geometry. No furniture-wide AABB is called a tabletop."""

from dataclasses import dataclass, asdict
import itertools
import mujoco
import numpy as np


def descendants(model, body):
    result = {body}
    for i in range(body + 1, model.nbody):
        if int(model.body_parentid[i]) in result:
            result.add(i)
    return result


def collision_geoms(sim, body):
    bodies = descendants(sim.model, body)
    return [
        g
        for g in range(sim.model.ngeom)
        if int(sim.model.geom_bodyid[g]) in bodies and (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g])
    ]


def geom_points(sim, g):
    m, d = sim.model, sim.data
    kind, size = int(m.geom_type[g]), m.geom_size[g]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = int(m.geom_dataid[g])
        a, n = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
        local = m.mesh_vert[a : a + n]
    elif kind == mujoco.mjtGeom.mjGEOM_BOX:
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * size
    elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * size[0]
    elif kind in (mujoco.mjtGeom.mjGEOM_CYLINDER, mujoco.mjtGeom.mjGEOM_CAPSULE):
        # Conservative bounding box, not a claim about exact cylinder footprint.
        half = size[1] + (size[0] if kind == mujoco.mjtGeom.mjGEOM_CAPSULE else 0)
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * [size[0], size[0], half]
    else:
        raise ValueError(f"unsupported object collision geom: {m.geom(g).name}")
    return local @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]


def body_points(sim, body):
    geoms = collision_geoms(sim, body)
    if not geoms:
        raise ValueError("object has no qualified collision geometry")
    return np.concatenate([geom_points(sim, g) for g in geoms])


@dataclass(frozen=True)
class SupportRegion:
    region_id: str
    support: str
    geom: str
    kind: str
    origin: list
    axes: list
    bounds: list
    height: float
    evidence: str

    def to_dict(self):
        return asdict(self)

    def local(self, points):
        return (np.asarray(points) - self.origin) @ np.asarray(self.axes)

    def contains(self, points, tolerance=0.0):
        """Every point's local xy lies inside the bounds; negative tolerance demands an inner margin."""
        local = self.local(points)
        a, b, c, d = self.bounds
        return bool(
            local[:, 0].min() >= a - tolerance
            and local[:, 0].max() <= b + tolerance
            and local[:, 1].min() >= c - tolerance
            and local[:, 1].max() <= d + tolerance
        )


def extract_regions(sim, support, geom_name=None):
    body = sim.model.body(support).id
    if sim.model.body_jntnum[body]:
        return []  # dynamic/complex support chains not qualified in v2
    result = []
    for g in collision_geoms(sim, body):
        ancestor = int(sim.model.geom_bodyid[g])
        moving = False
        while ancestor:
            moving |= bool(sim.model.body_jntnum[ancestor])
            ancestor = int(sim.model.body_parentid[ancestor])
        if moving:
            continue
        name = sim.model.geom(g).name
        if not name or geom_name is not None and name != geom_name:
            continue
        kind = sim.model.geom_type[g]
        axes = sim.data.geom_xmat[g].reshape(3, 3).copy()
        if kind == mujoco.mjtGeom.mjGEOM_BOX and np.abs(axes[2]).max() > 0.999:
            vertical = int(np.abs(axes[2]).argmax())
            tangent = [i for i in range(3) if i != vertical]
            u, v = axes[:, tangent[0]], axes[:, tangent[1]]
            if np.cross(u, v)[2] < 0:
                v = -v
            axes = np.column_stack((u, v, np.array([0.0, 0.0, 1.0])))
            height = sim.data.geom_xpos[g, 2] + sim.model.geom_size[g, vertical]
            size = sim.model.geom_size[g, tangent]
            bounds = [-size[0], size[0], -size[1], size[1]]
            evidence = "horizontal_collision_box_top_any_local_axis"
        elif kind == mujoco.mjtGeom.mjGEOM_MESH:
            mesh = int(sim.model.geom_dataid[g])
            a, n = int(sim.model.mesh_faceadr[mesh]), int(sim.model.mesh_facenum[mesh])
            vertices = geom_points(sim, g)
            triangles = vertices[sim.model.mesh_face[a : a + n]]
            # Winding may vary: flatness plus downward support rays confirm actual surface.
            area = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2]
            flat = (np.ptp(triangles[:, :, 2], axis=1) < 0.001) & (np.abs(area) > 1e-8)
            if not flat.any():
                continue
            height = float(triangles[flat, :, 2].mean(axis=1).max())
            top = triangles[flat & (np.abs(triangles[:, :, 2].mean(axis=1) - height) < 0.001)].reshape(-1, 3)
            axes = np.eye(3)
            local = top - sim.data.geom_xpos[g]
            bounds = [local[:, 0].min(), local[:, 0].max(), local[:, 1].min(), local[:, 1].max()]
            evidence = "horizontal_mesh_triangles_plus_per_placement_support_rays"
        else:
            continue
        if (bounds[1] - bounds[0]) < 0.05 or (bounds[3] - bounds[2]) < 0.05:
            continue
        origin = sim.data.geom_xpos[g].copy()
        origin[2] = height
        result.append(
            SupportRegion(
                f"{support}:{name}:top",
                support,
                name,
                "plane",
                origin.tolist(),
                axes.tolist(),
                np.asarray(bounds).tolist(),
                float(height),
                evidence,
            )
        )
    return sorted(result, key=lambda r: (-(r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]), r.region_id))


def quat_yaw(yaw):
    return np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)])


def rotated_extent(width, depth, yaw):
    """Axis-aligned extent of a width x depth footprint rotated by yaw."""
    c, s = abs(np.cos(yaw)), abs(np.sin(yaw))
    return c * width + s * depth, s * width + c * depth


def quat_angle(a, b):
    return float(2 * np.arccos(np.clip(abs(np.dot(a, b)), 0, 1)))


def support_rays(sim, body, region, points):
    """Sample corners/edges/center. Cast against selected support geom only, including mesh holes."""
    g = sim.model.geom(region.geom).id
    local = region.local(points)
    lo, hi = local[:, :2].min(axis=0), local[:, :2].max(axis=0)
    hits = []
    for x, y in itertools.product(np.linspace(lo[0], hi[0], 5), np.linspace(lo[1], hi[1], 5)):
        xyz = np.asarray(region.origin) + np.asarray(region.axes) @ [x, y, 0.03]
        direction = np.array([0.0, 0.0, -1.0])
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            distance = mujoco.mj_rayMesh(sim.model, sim.data, g, xyz, direction)
        else:
            distance = mujoco.mju_rayGeom(
                sim.data.geom_xpos[g],
                sim.data.geom_xmat[g],
                sim.model.geom_size[g],
                xyz,
                direction,
                sim.model.geom_type[g],
            )
        hits.append(bool(distance >= 0 and abs(distance - 0.03) < 0.002))
    return all(hits)


def body_pose(sim, body):
    result = np.eye(4)
    result[:3, 3] = sim.data.xpos[body]
    result[:3, :3] = sim.data.xmat[body].reshape(3, 3)
    return result


def tcp_pose(sim, side):
    """World pose of the robot's tool-centre site for one arm."""
    site = sim.robot.tcp_site(side)
    pose = np.eye(4)
    pose[:3, 3] = sim.data.site_xpos[site]
    pose[:3, :3] = sim.data.site_xmat[site].reshape(3, 3)
    return pose


def pose7(matrix):
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.ascontiguousarray(matrix[:3, :3]).reshape(-1))
    return np.r_[matrix[:3, 3], q].tolist()


def room_triangles(sim):
    """使用已编译的 room floor mesh，坐标自动随 MJCF 变换；不假设 THOR 轴。"""
    m, d = sim.model, sim.data
    result = []
    for g in range(m.ngeom):
        name = m.geom(g).name.lower()
        if not name.startswith("room_") or m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        mesh = int(m.geom_dataid[g])
        va, vn = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
        fa, fn = int(m.mesh_faceadr[mesh]), int(m.mesh_facenum[mesh])
        verts = m.mesh_vert[va : va + vn] @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        faces = m.mesh_face[fa : fa + fn]
        triangles = verts[faces][:, :, :2]
        a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        area = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        result.extend(triangles[np.abs(area) > 1e-8])
    return np.asarray(result)


def inside_triangles(xy, triangles):
    if len(triangles) == 0:
        return True
    u, v = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    p = xy - triangles[:, 0]
    det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    a = (p[:, 0] * v[:, 1] - p[:, 1] * v[:, 0]) / det
    b = (u[:, 0] * p[:, 1] - u[:, 1] * p[:, 0]) / det
    return bool(np.any((a >= 0) & (b >= 0) & (a + b <= 1)))


def floor_height(sim, xy, *, triangles=None):
    """Ray against named collision floor, not robot or room visual occluders.

    The room-triangle mask supplies bounded coverage for infinite floor planes.
    """
    start = np.r_[xy, 0.20]
    direction = np.array([0.0, 0.0, -1.0])
    distances = []
    for g in range(sim.model.ngeom):
        if "floor" not in sim.model.geom(g).name.lower():
            continue
        if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
            continue
        if sim.model.body(int(sim.model.geom_bodyid[g])).name.startswith("robot_0/"):
            continue
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            distance = mujoco.mj_rayMesh(sim.model, sim.data, g, start, direction)
        else:
            distance = mujoco.mju_rayGeom(
                sim.data.geom_xpos[g],
                sim.data.geom_xmat[g],
                sim.model.geom_size[g],
                start,
                direction,
                sim.model.geom_type[g],
            )
        if 0 <= distance <= 0.30:
            distances.append(float(distance))
    if distances and inside_triangles(np.asarray(xy), room_triangles(sim) if triangles is None else triangles):
        return 0.20 - min(distances)
    return None


def floor_support(sim, xy, *, triangles=None):
    return floor_height(sim, xy, triangles=triangles) is not None
