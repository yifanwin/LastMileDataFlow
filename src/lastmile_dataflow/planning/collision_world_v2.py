"""V2 per-geom scene export; separate target checker never disables robot links."""
import itertools
import numpy as np
import mujoco
from ..utils.io import write_json
from ..scenes.geometry import descendants
from ..scenes.geometry import body_pose


_mesh_ids = itertools.count()

def collision_world(sim,path,range_m=2., *, reference_base=None, target_only=False, bodies=None, filename=None):
    """Per-geom collision mesh, never use furniture-wide AABB as exact geometry.

    `bodies` restricts `target_only` to a body set (e.g. the handle alone);
    `filename` names the diagnostic JSON so split worlds do not overwrite.
    """
    import trimesh
    from curobo._src.geom.types import SceneCfg, Mesh
    m,d=sim.model,sim.data
    base=body_pose(sim,m.body('robot_0/base').id) if reference_base is None else reference_base
    inv=np.linalg.inv(base)
    target=descendants(m,sim.target_id) if bodies is None else bodies; vertices=[]; faces=[]; names=[]; count=0
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
            mesh=int(m.geom_dataid[g]); va,vn=int(m.mesh_vertadr[mesh]),int(m.mesh_vertnum[mesh]); fa,fn=int(m.mesh_faceadr[mesh]),int(m.mesh_facenum[mesh])
            v=m.mesh_vert[va:va+vn].copy(); f=m.mesh_face[fa:fa+fn].copy()
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
        world=v@d.geom_xmat[g].reshape(3,3).T+d.geom_xpos[g]
        vertices.append((np.c_[world,np.ones(len(v))]@inv.T)[:,:3]); faces.append(f+count); count+=len(v); names.append(name)
    report = path/(filename or ('target_world.json' if target_only else 'collision_world.json'))
    if not vertices:
        write_json(report,{'frame':'measured base','geoms':[],
                   'target_excluded':'separate target-only checker with allowed finger mask'})
        return SceneCfg()
    write_json(report,{'frame':'measured base','geoms':names,'vertices':count,
                 'target_excluded':'separate target-only checker with allowed finger mask','floor_excluded':'only planner; physics unchanged',
                 'carried_object':'attached geometry during lift; no physical welding; contacts independently checked',
                 'primitive_note':'box/mesh exact; curved primitives tessellated'})
    vertex_array, face_array = np.concatenate(vertices), np.concatenate(faces)
    # V2 caches Warp meshes by name. Geometry baked in this measured frame
    # changes for articulated objects; do not reuse a stale mesh by fixed name.
    name = f"{'target' if target_only else 'environment'}_{next(_mesh_ids)}"
    return SceneCfg(mesh=[Mesh(name=name,pose=[0,0,0,1,0,0,0],vertices=vertex_array.tolist(),faces=face_array.tolist())])
