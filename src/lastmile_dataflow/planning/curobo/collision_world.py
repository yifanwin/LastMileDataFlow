"""V2 per-geom scene export; separate target checker never disables robot links."""
import itertools
import numpy as np
import mujoco
from ...utils.io import write_json
from ...scenes.geometry import descendants
from ...scenes.geometry import body_pose


_mesh_ids = itertools.count()

def exported_geoms(sim, base, range_m, *, target_only=False, bodies=None):
    """Yield (geom id, world vertices, faces) of every geom one planner world contains.

    The single selection rule shared by `collision_world` and diagnostics:
    robot, floor, non-colliding and out-of-range (XY enclosing radius) geoms
    are skipped; `bodies` (default: target subtree) are excluded, or are the
    only geoms kept when `target_only`. Every yielded mesh is closed: primitives
    are tessellated, mesh geoms are their convex hull as MuJoCo collides them.
    """
    import trimesh
    m,d=sim.model,sim.data
    target=descendants(m,sim.target_id) if bodies is None else bodies
    for g in range(m.ngeom):
        b=int(m.geom_bodyid[g]); name=m.geom(g).name
        if m.body(b).name.startswith('robot_0/') or ((b not in target) if target_only else (b in target)) or not (m.geom_contype[g] or m.geom_conaffinity[g]): continue
        kind=m.geom_type[g]; size=m.geom_size[g]
        if 'floor' in name.lower() or kind==mujoco.mjtGeom.mjGEOM_PLANE: continue
        # enclosing radius is a conservative local inclusion test, not collision geometry
        if np.linalg.norm(d.geom_xpos[g,:2]-base[:2,3])-m.geom_rbound[g]>range_m: continue
        if g in sim.planning_geometry:
            v, f = sim.planning_geometry[g]
        elif kind==mujoco.mjtGeom.mjGEOM_MESH:
            # MuJoCo collides a mesh geom as its convex hull; export that closed hull.
            # cuRobo 0.8.0 signs distances with Warp ray parity, reliable only on
            # watertight meshes: open raw meshes merged into one world turn free
            # space into deep "inside", depending on the frame.
            mesh=int(m.geom_dataid[g]); va,vn=int(m.mesh_vertadr[mesh]),int(m.mesh_vertnum[mesh])
            hull=trimesh.convex.convex_hull(m.mesh_vert[va:va+vn])
            v,f=np.asarray(hull.vertices),np.asarray(hull.faces)
        else:
            if kind==mujoco.mjtGeom.mjGEOM_BOX: obj=trimesh.creation.box(extents=2*size)
            elif kind in (mujoco.mjtGeom.mjGEOM_SPHERE,mujoco.mjtGeom.mjGEOM_ELLIPSOID):
                obj=trimesh.creation.icosphere(subdivisions=2,radius=size[0] if kind==mujoco.mjtGeom.mjGEOM_SPHERE else 1.)
                if kind==mujoco.mjtGeom.mjGEOM_ELLIPSOID: obj.vertices*=size
            elif kind==mujoco.mjtGeom.mjGEOM_CYLINDER: obj=trimesh.creation.cylinder(radius=size[0],height=2*size[1],sections=32)
            elif kind==mujoco.mjtGeom.mjGEOM_CAPSULE: obj=trimesh.creation.capsule(radius=size[0],height=2*size[1])
            else: raise ValueError(f'unsupported collision geometry {name}')
            v,f=np.asarray(obj.vertices),np.asarray(obj.faces)
        sim.planning_geometry[g] = v, f
        yield g, v@d.geom_xmat[g].reshape(3,3).T+d.geom_xpos[g], f


def collision_world(sim,path,range_m=2., *, reference_base=None, target_only=False, bodies=None, filename=None,
                    cuboids=None, max_cuboids=0):
    """Per-geom collision mesh, never use furniture-wide AABB as exact geometry.

    `bodies` restricts `target_only` to a body set (e.g. the handle alone);
    `filename` names the diagnostic JSON so split worlds do not overwrite.
    `cuboids`: XY reach (m, same enclosing-radius test as `range_m`) within which
    native box geoms become posed cuRobo cuboids (exact, queried per obstacle, so
    overlapping boxes keep their own inside); every other geom is still merged
    into one mesh of closed hulls. Each cuboid costs a query per robot sphere, so
    only boxes the robot can reach are worth it; beyond the nearest `max_cuboids`
    of them the rest also stay in the mesh.
    """
    from curobo._src.geom.types import SceneCfg, Mesh, Cuboid
    m,d=sim.model,sim.data
    base=body_pose(sim,m.body('robot_0/base').id) if reference_base is None else reference_base
    inv=np.linalg.inv(base); vertices=[]; faces=[]; names=[]; count=0; boxes=[]
    exported=list(exported_geoms(sim, base, range_m, target_only=target_only, bodies=bodies))
    reach=lambda g: np.linalg.norm(d.geom_xpos[g,:2]-base[:2,3])-m.geom_rbound[g]
    near=sorted((g for g,_,_ in exported if cuboids is not None and m.geom_type[g]==mujoco.mjtGeom.mjGEOM_BOX
                 and reach(g)<=cuboids), key=reach)[:max_cuboids]
    for g, world, f in exported:
        if g in near:
            quat=np.zeros(4); mujoco.mju_mat2Quat(quat,(inv[:3,:3]@d.geom_xmat[g].reshape(3,3)).ravel())
            boxes.append(Cuboid(name=f'cuboid_{g}', pose=[*(inv[:3,:3]@d.geom_xpos[g]+inv[:3,3]), *quat],
                                dims=(2*m.geom_size[g]).tolist()))
            continue
        vertices.append((np.c_[world,np.ones(len(world))]@inv.T)[:,:3]); faces.append(f+count); count+=len(world); names.append(m.geom(g).name)
    report = path/(filename or ('target_world.json' if target_only else 'collision_world.json'))
    cuboid_names = [m.geom(int(b.name.split('_')[1])).name for b in boxes]
    if not vertices:
        write_json(report,{'frame':'measured base','geoms':[],'cuboids':cuboid_names,
                   'target_excluded':'separate target-only checker with allowed finger mask'})
        return SceneCfg(cuboid=boxes) if boxes else SceneCfg()
    write_json(report,{'frame':'measured base','geoms':names,'vertices':count,'cuboids':cuboid_names,
                 'target_excluded':'separate target-only checker with allowed finger mask','floor_excluded':'only planner; physics unchanged',
                 'carried_object':'attached geometry during lift; no physical welding; contacts independently checked',
                 'primitive_note':'box/mesh exact; curved primitives tessellated'})
    vertex_array, face_array = np.concatenate(vertices), np.concatenate(faces)
    # V2 caches Warp meshes by name. Geometry baked in this measured frame
    # changes for articulated objects; do not reuse a stale mesh by fixed name.
    name = f"{'target' if target_only else 'environment'}_{next(_mesh_ids)}"
    return SceneCfg(mesh=[Mesh(name=name,pose=[0,0,0,1,0,0,0],vertices=vertex_array.tolist(),faces=face_array.tolist())],
                    cuboid=boxes or None)
