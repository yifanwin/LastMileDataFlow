"""Native collision meshes in a declared frame; primitive tessellation is explicit."""
import numpy as np
import mujoco
from ..scenes.geometry import collision_geoms


def geom_mesh(sim,g,frame):
    import trimesh
    m,d=sim.model,sim.data; kind=m.geom_type[g]; size=m.geom_size[g]
    if kind==mujoco.mjtGeom.mjGEOM_MESH:
        k=int(m.geom_dataid[g]); a,n=int(m.mesh_vertadr[k]),int(m.mesh_vertnum[k]); f,nf=int(m.mesh_faceadr[k]),int(m.mesh_facenum[k])
        vertices=m.mesh_vert[a:a+n].copy(); faces=m.mesh_face[f:f+nf].copy()
    else:
        if kind==mujoco.mjtGeom.mjGEOM_BOX: mesh=trimesh.creation.box(extents=2*size)
        elif kind==mujoco.mjtGeom.mjGEOM_SPHERE: mesh=trimesh.creation.icosphere(subdivisions=2,radius=size[0])
        elif kind==mujoco.mjtGeom.mjGEOM_CYLINDER: mesh=trimesh.creation.cylinder(radius=size[0],height=2*size[1],sections=32)
        elif kind==mujoco.mjtGeom.mjGEOM_CAPSULE: mesh=trimesh.creation.capsule(radius=size[0],height=2*size[1])
        else: raise ValueError('unsupported grasp collision primitive')
        vertices,faces=np.asarray(mesh.vertices),np.asarray(mesh.faces)
    world=vertices@d.geom_xmat[g].reshape(3,3).T+d.geom_xpos[g]
    vertices=(world-frame[:3,3])@frame[:3,:3]
    # Native contact parameters travel with the mesh so isolated models do not invent friction/softness.
    contact={'friction':m.geom_friction[g].tolist(),'condim':int(m.geom_condim[g]),'solref':m.geom_solref[g].tolist(),
             'solimp':m.geom_solimp[g].tolist(),'margin':float(m.geom_margin[g]),'gap':float(m.geom_gap[g])}
    return {'vertices':vertices.tolist(),'faces':faces.tolist(),'source_geom':m.geom(g).name,'contact':contact}


def object_meshes(sim,body):
    from ..planning.curobo import body_pose
    return [geom_mesh(sim,g,body_pose(sim,body)) for g in collision_geoms(sim,body)]


def sample_surface(meshes,*,count=512,seed=0):
    vertices=[]; faces=[]; offset=0
    for mesh in meshes:
        vertices.extend(mesh['vertices']); faces.extend((np.array(mesh['faces'])+offset).tolist()); offset+=len(mesh['vertices'])
    vertices=np.asarray(vertices); faces=np.asarray(faces); tri=vertices[faces]
    normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]); areas=np.linalg.norm(normals,axis=1)
    if areas.sum()<=0: raise ValueError('degenerate object surface')
    rng=np.random.default_rng(seed); ids=rng.choice(len(faces),size=count,p=areas/areas.sum())
    uv=rng.random((count,2)); uv[uv.sum(axis=1)>1]=1-uv[uv.sum(axis=1)>1]
    selected=tri[ids]; points=selected[:,0]+uv[:,0,None]*(selected[:,1]-selected[:,0])+uv[:,1,None]*(selected[:,2]-selected[:,0])
    return points,normals[ids]/areas[ids,None]


def surface_area(meshes):
    total=0.
    for mesh in meshes:
        tri=np.asarray(mesh['vertices'])[np.asarray(mesh['faces'])]
        total+=float(np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1).sum()/2)
    return total


def density_count(meshes,*,spacing_m=.003,minimum=4000,maximum=60000):
    """Point count for roughly uniform spacing; sparse samples fake material gaps in pinch slicing."""
    return int(np.clip(surface_area(meshes)/spacing_m**2,minimum,maximum))


def asset_collision_digest(meshes):
    """Ignore instance geom names and round inverse-transform numerical noise."""
    from ..io import digest
    geometry=[]
    for mesh in meshes:
        vertices=np.round(np.array(mesh['vertices']),6); vertices[np.abs(vertices)<.5e-6]=0.
        geometry.append({'vertices':vertices.tolist(),'faces':mesh['faces']})
    return digest(sorted(geometry,key=digest))
