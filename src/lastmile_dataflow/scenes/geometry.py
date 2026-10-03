"""Conservative collision geometry. No furniture-wide AABB is called a tabletop."""
from dataclasses import dataclass, asdict
import itertools
import mujoco
import numpy as np


def descendants(model, body):
    result = {body}
    for i in range(body + 1, model.nbody):
        if int(model.body_parentid[i]) in result: result.add(i)
    return result


def collision_geoms(sim, body):
    bodies = descendants(sim.model, body)
    return [g for g in range(sim.model.ngeom) if int(sim.model.geom_bodyid[g]) in bodies
            and (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g])]


def geom_points(sim, g):
    m, d = sim.model, sim.data
    kind, size = m.geom_type[g], m.geom_size[g]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = int(m.geom_dataid[g]); a, n = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
        local = m.mesh_vert[a:a+n]
    elif kind == mujoco.mjtGeom.mjGEOM_BOX:
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * size
    elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * size[0]
    elif kind in (mujoco.mjtGeom.mjGEOM_CYLINDER, mujoco.mjtGeom.mjGEOM_CAPSULE):
        # Conservative bounding box, not a claim about exact cylinder footprint.
        half = size[1] + (size[0] if kind == mujoco.mjtGeom.mjGEOM_CAPSULE else 0)
        local = np.array(list(itertools.product((-1, 1), repeat=3))) * [size[0], size[0], half]
    else:
        raise ValueError(f'unsupported object collision geom: {m.geom(g).name}')
    return local @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]


def body_points(sim, body):
    geoms = collision_geoms(sim, body)
    if not geoms: raise ValueError('object has no qualified collision geometry')
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

    def to_dict(self): return asdict(self)

    def local(self, points):
        return (np.asarray(points) - self.origin) @ np.asarray(self.axes)


def extract_regions(sim, support, geom_name=None):
    body = sim.model.body(support).id
    if sim.model.body_jntnum[body]: return []  # dynamic/complex support chains not qualified in v2
    result = []
    for g in collision_geoms(sim, body):
        ancestor=int(sim.model.geom_bodyid[g]); moving=False
        while ancestor:
            moving |= bool(sim.model.body_jntnum[ancestor]); ancestor=int(sim.model.body_parentid[ancestor])
        if moving: continue
        name = sim.model.geom(g).name
        if not name or geom_name is not None and name != geom_name: continue
        kind = sim.model.geom_type[g]
        axes = sim.data.geom_xmat[g].reshape(3, 3).copy()
        if kind == mujoco.mjtGeom.mjGEOM_BOX and np.abs(axes[2]).max() > .999:
            vertical = int(np.abs(axes[2]).argmax())
            tangent = [i for i in range(3) if i != vertical]
            u, v = axes[:,tangent[0]], axes[:,tangent[1]]
            if np.cross(u,v)[2] < 0: v = -v
            axes = np.column_stack((u,v,np.array([0.,0.,1.])))
            height = sim.data.geom_xpos[g, 2] + sim.model.geom_size[g, vertical]
            size = sim.model.geom_size[g, tangent]
            bounds = [-size[0], size[0], -size[1], size[1]]
            evidence = 'horizontal_collision_box_top_any_local_axis'
        elif kind == mujoco.mjtGeom.mjGEOM_MESH:
            mesh = int(sim.model.geom_dataid[g]); a, n = int(sim.model.mesh_faceadr[mesh]), int(sim.model.mesh_facenum[mesh])
            vertices = geom_points(sim, g)
            triangles = vertices[sim.model.mesh_face[a:a+n]]
            # Winding may vary: flatness plus downward support rays confirm actual surface.
            area = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])[:,2]
            flat = (np.ptp(triangles[:, :, 2], axis=1) < .001) & (np.abs(area)>1e-8)
            if not flat.any(): continue
            height = float(triangles[flat, :, 2].mean(axis=1).max())
            top = triangles[flat & (np.abs(triangles[:, :, 2].mean(axis=1)-height) < .001)].reshape(-1, 3)
            axes = np.eye(3)
            local = top - sim.data.geom_xpos[g]
            bounds = [local[:,0].min(), local[:,0].max(), local[:,1].min(), local[:,1].max()]
            evidence = 'horizontal_mesh_triangles_plus_per_placement_support_rays'
        else: continue
        if (bounds[1]-bounds[0]) < .05 or (bounds[3]-bounds[2]) < .05: continue
        origin = sim.data.geom_xpos[g].copy(); origin[2] = height
        result.append(SupportRegion(f'{support}:{name}:top', support, name, 'plane', origin.tolist(), axes.tolist(),
                                    np.asarray(bounds).tolist(), float(height), evidence))
    return sorted(result, key=lambda r: (-(r.bounds[1]-r.bounds[0])*(r.bounds[3]-r.bounds[2]), r.region_id))


def quat_yaw(yaw): return np.array([np.cos(yaw/2), 0, 0, np.sin(yaw/2)])


def quat_angle(a, b):
    return float(2*np.arccos(np.clip(abs(np.dot(a, b)), 0, 1)))


def support_rays(sim, body, region, points):
    """Sample corners/edges/center. Cast against selected support geom only, including mesh holes."""
    g = sim.model.geom(region.geom).id
    local = region.local(points)
    lo, hi = local[:, :2].min(axis=0), local[:, :2].max(axis=0)
    hits = []
    for x, y in itertools.product(np.linspace(lo[0], hi[0], 5), np.linspace(lo[1], hi[1], 5)):
        xyz = np.asarray(region.origin) + np.asarray(region.axes) @ [x, y, .03]
        direction = np.array([0., 0., -1.])
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            distance = mujoco.mj_rayMesh(sim.model, sim.data, g, xyz, direction)
        else:
            distance = mujoco.mju_rayGeom(sim.data.geom_xpos[g], sim.data.geom_xmat[g], sim.model.geom_size[g], xyz, direction, sim.model.geom_type[g])
        hits.append(bool(distance >= 0 and abs(distance-.03) < .002))
    return all(hits)


def quat_from_matrix(rotation):
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.asarray(rotation, dtype=float).reshape(9))
    return quat


def placement_pose_keeping_orientation(sim, body, region, xy, dyaw=0., gap=.003, margin=.01):
    """R_new = Rz(dyaw) @ R_current: world yaw change, asset's own roll/pitch preserved.

    placement_pose() rewrites the orientation as a pure world yaw, which lays y-up THOR assets on
    their side; the case construction paths use this variant instead. Footprint margin and
    downward support-ray checks are identical to placement_pose.
    """
    points = body_points(sim, body)
    bpos, bmat = sim.data.xpos[body], sim.data.xmat[body].reshape(3,3)
    local = (points-bpos) @ bmat
    c, s = np.cos(dyaw), np.sin(dyaw)
    rotation = np.array([[c,-s,0],[s,c,0],[0,0,1.]]) @ bmat
    rotated = local @ rotation.T
    center = np.asarray(region.origin) + np.asarray(region.axes) @ [*xy, 0]
    center[2] = region.height - rotated[:,2].min() + gap
    projected = rotated + center
    footprint = region.local(projected)
    a,b,c,d = region.bounds
    if not (footprint[:,0].min() >= a+margin and footprint[:,0].max() <= b-margin
            and footprint[:,1].min() >= c+margin and footprint[:,1].max() <= d-margin):
        return None
    if not support_rays(sim, body, region, projected): return None
    return [*center.tolist(), *quat_from_matrix(rotation).tolist()]


def geom_local_bounds(sim, g):
    """Conservative AABB of a collision geom in its own frame; meshes use their vertices."""
    m = sim.model
    kind, size = m.geom_type[g], m.geom_size[g]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = int(m.geom_dataid[g]); a, n = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
        vertices = m.mesh_vert[a:a+n]
        return vertices.min(axis=0).copy(), vertices.max(axis=0).copy()
    if kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        return np.full(3, -size[0]), np.full(3, size[0])
    if kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
        return np.array([-size[0], -size[0], -size[1]]), np.array([size[0], size[0], size[1]])
    if kind == mujoco.mjtGeom.mjGEOM_CAPSULE:
        half = size[1] + size[0]
        return np.array([-size[0], -size[0], -half]), np.array([size[0], size[0], half])
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        return -size.copy(), size.copy()
    return None


def obstacle_geoms(sim, exclude_bodies):
    """Collision geoms that can obstruct a standing robot, in one precomputed list."""
    m = sim.model
    return [g for g in range(m.ngeom)
            if int(m.geom_bodyid[g]) not in exclude_bodies
            and not m.body(int(m.geom_bodyid[g])).name.startswith('robot_0/')
            and (m.geom_contype[g] or m.geom_conaffinity[g])
            and m.geom_type[g] != mujoco.mjtGeom.mjGEOM_PLANE]


def _geom_world_bounds(sim, g, entry):
    """Conservative world AABB of one geom from its local AABB: the box is rotated, not re-fit."""
    low, high = entry
    centre = sim.data.geom_xpos[g]
    rotation = sim.data.geom_xmat[g].reshape(3, 3)
    mid = (low + high) / 2; half = (high - low) / 2
    reach = np.abs(rotation) @ half
    centre = centre + rotation @ mid
    return centre - reach, centre + reach


def free_space_distance(sim, point, radius, *, geoms, bounds, heights=(.05, .20, .35, .50, .65, .80)):
    """Free horizontal distance around a standing position: ray fan over the robot's body volume.

    The fan is cast at several heights inside the standing volume and the smallest hit is taken.
    This is what a bounding sphere cannot do: one wide wall mesh makes its bounding sphere cover a
    whole room, so every nearby point reads as fully blocked, while the thin wall mesh itself is
    metres away along most directions. Sampling several heights also keeps a low slab (a tabletop
    apron, the floor plane) from being mistaken for a floor-level obstruction.
    Geometric proxy for "is this side clear", never navigation evidence.
    """
    m, d = sim.model, sim.data
    point = np.asarray(point, dtype=float)
    best = float(radius)
    for height in heights:
        start = np.array([point[0], point[1], float(point[2]) + height])
        for g in geoms:
            low, high = _geom_world_bounds(sim, g, bounds[g])
            if high[2] < start[2] or low[2] > start[2]: continue
            # Cheap rejection against the geom's conservative world AABB before any ray is cast.
            gap = float(np.linalg.norm(np.maximum(np.maximum(low[:2] - start[:2], start[:2] - high[:2]), 0.)))
            if gap >= best: continue
            for index in range(24):
                angle = 2 * np.pi * index / 24
                direction = np.array([np.cos(angle), np.sin(angle), 0.])
                if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                    distance = mujoco.mj_rayMesh(m, d, g, start, direction)
                else:
                    distance = mujoco.mju_rayGeom(d.geom_xpos[g], d.geom_xmat[g], m.geom_size[g],
                                                  start, direction, m.geom_type[g])
                if 0. <= distance < best: best = float(distance)
                if best <= 0.: return 0.
    return best


def placement_pose(sim, body, region, xy, yaw, gap=.003):
    points = body_points(sim, body)
    bpos, bmat = sim.data.xpos[body], sim.data.xmat[body].reshape(3,3)
    local = (points-bpos) @ bmat
    c, s = np.cos(yaw), np.sin(yaw)
    rot = np.array([[c,-s,0],[s,c,0],[0,0,1]])
    rotated = local @ rot.T
    center = np.asarray(region.origin) + np.asarray(region.axes) @ [*xy, 0]
    center[2] = region.height - rotated[:,2].min() + gap
    projected = rotated + center
    footprint = region.local(projected)
    a,b,c,d = region.bounds
    if not (footprint[:,0].min() >= a+.01 and footprint[:,0].max() <= b-.01 and footprint[:,1].min() >= c+.01 and footprint[:,1].max() <= d-.01):
        return None
    if not support_rays(sim, body, region, projected): return None
    return np.r_[center, quat_yaw(yaw)].tolist()
