"""Native cuRobo MotionGen: measured-base coordinates and single legal active arm."""
import copy
from pathlib import Path
import numpy as np
import mujoco
from ..io import file_digest, write_json
from ..integrations.waypoints import PlanResult
from ..scenes.geometry import descendants, geom_points


def body_pose(sim,body):
    result=np.eye(4); result[:3,3]=sim.data.xpos[body]; result[:3,:3]=sim.data.xmat[body].reshape(3,3)
    return result


def tcp_pose(sim,side):
    site=sim.model.site('robot_0/ee_site_'+side[0]).id
    pose=np.eye(4); pose[:3,3]=sim.data.site_xpos[site]; pose[:3,:3]=sim.data.site_xmat[site].reshape(3,3)
    return pose


def pose7(matrix):
    q=np.zeros(4); mujoco.mju_mat2Quat(q,np.ascontiguousarray(matrix[:3,:3]).reshape(-1))
    return np.r_[matrix[:3,3],q].tolist()


def load_grasps(config,sim):
    if file_digest(config.grasp_path)!=config.grasp_sha256: raise ValueError('grasp file modified')
    entries=[e for e in sim.catalog if e['instance_id']==config.target and e['asset_id']==config.asset_id]
    if len(entries)!=1: raise ValueError('target/asset/grasp identity mismatch')
    with np.load(config.grasp_path,allow_pickle=False) as z: transforms=z['transforms'].astype(float)
    if transforms.ndim!=3 or transforms.shape[1:]!=(4,4) or not np.isfinite(transforms).all(): raise ValueError('invalid grasp transforms')
    if max(config.grasp_ids)>=len(transforms): raise ValueError('grasp row out of bounds')
    selected={}
    for index in config.grasp_ids:
        pose=transforms[index].copy()
        if not np.allclose(pose[3],[0,0,0,1],atol=1e-5) or np.linalg.det(pose[:3,:3])<=0: raise ValueError('invalid grasp rotation')
        u,s,v=np.linalg.svd(pose[:3,:3])
        if np.max(np.abs(s-1))>.02: raise ValueError('grasp rotation not near SO(3)')
        pose[:3,:3]=u@v  # deterministic projection for float16 source quantization
        selected[index]=pose
    return selected


def locked_arm_config(template,sim,side,root):
    cfg=copy.deepcopy(template); k=cfg['robot_cfg']['kinematics']; idle='right' if side=='left' else 'left'
    active=[f'{side}_arm_{i}' for i in range(7)]
    locks=dict(k.get('lock_joints') or {})
    for group in ('torso','head',idle+'_arm','left_gripper','right_gripper'):
        locks.update({n:float(q) for n,q in zip(sim.robot.groups[group],sim.robot.group(group))})
    locks.update(dict.fromkeys(('base_x','base_y','base_theta'),0.))
    for n in active: locks.pop(n,None)
    k.update(lock_joints=locks,ee_link=f'ee_{side}_tcp',urdf_path=str(root/'urdf/model_holobase.urdf'),
             asset_root_path=str(root/'urdf/meshes'),collision_spheres=str(root/'rby1m_holobase_spheres.yml'),usd_robot_root=str(root))
    k['link_names']=list(dict.fromkeys(k.get('link_names',[])+['link_head_2']))
    original=k['cspace']['joint_names']; ids=[original.index(n) for n in active]
    for n,v in k['cspace'].items():
        if isinstance(v,list) and len(v)==len(original): k['cspace'][n]=[v[i] for i in ids]
    k['cspace']['joint_names']=active
    return cfg


def collision_world(sim,path,range_m=2.):
    """Per-geom collision mesh, never use furniture-wide AABB as exact geometry."""
    import trimesh
    from curobo.geom.types import WorldConfig,Mesh
    m,d=sim.model,sim.data; base=body_pose(sim,m.body('robot_0/base').id); inv=np.linalg.inv(base)
    target=descendants(m,sim.target_id); vertices=[]; faces=[]; names=[]; count=0
    for g in range(m.ngeom):
        b=int(m.geom_bodyid[g]); name=m.geom(g).name
        if m.body(b).name.startswith('robot_0/') or b in target or not (m.geom_contype[g] or m.geom_conaffinity[g]): continue
        kind=m.geom_type[g]; size=m.geom_size[g]
        if 'floor' in name.lower() or kind==mujoco.mjtGeom.mjGEOM_PLANE: continue
        # enclosing radius is a conservative local inclusion test, not collision geometry
        if np.linalg.norm(d.geom_xpos[g,:2]-base[:2,3])-m.geom_rbound[g]>range_m: continue
        if kind==mujoco.mjtGeom.mjGEOM_MESH:
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
        world=v@d.geom_xmat[g].reshape(3,3).T+d.geom_xpos[g]
        vertices.append((np.c_[world,np.ones(len(v))]@inv.T)[:,:3]); faces.append(f+count); count+=len(v); names.append(name)
    if not vertices: raise ValueError('empty local collision world')
    write_json(path/'collision_world.json',{'frame':'measured base','geoms':names,'vertices':count,
                 'target_excluded':'intended finger contact, checked in physics','floor_excluded':'only planner; physics unchanged',
                 'carried_object':'not attached in planner; all target/environment contacts checked in physical execution',
                 'primitive_note':'box/mesh exact; curved primitives tessellated'})
    return WorldConfig(mesh=[Mesh(name='environment',pose=[0,0,0,1,0,0,0],vertices=np.concatenate(vertices).tolist(),faces=np.concatenate(faces).tolist())])


class NativePlanner:
    def __init__(self,sim,config,side,path):
        import torch
        import yaml
        from curobo.geom.sdf.world import CollisionCheckerType
        from curobo.types.base import TensorDeviceType
        from curobo.wrap.reacher.motion_gen import MotionGen,MotionGenConfig,MotionGenPlanConfig
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; no CPU substitute for cuRobo')
        torch.manual_seed(config.seed)
        self.sim,self.config,self.side=sim,config,side; self.args=TensorDeviceType()
        root=Path(config.robot_planner_dir); path=Path(path)
        cfg=locked_arm_config(yaml.safe_load((root/'rby1m_holobase.yml').read_text()),sim,side,root)
        (path/'planner.yml').write_text(yaml.safe_dump(cfg,sort_keys=False))
        write_json(path/'planner_assets.json',{str(p):file_digest(p) for p in (root/'rby1m_holobase.yml',root/'rby1m_holobase_spheres.yml',root/'urdf/model_holobase.urdf')})
        self.names=tuple(f'{side}_arm_{i}' for i in range(7))
        settings=MotionGenConfig.load_from_robot_config(cfg['robot_cfg'],collision_world(sim,path),self.args,
            collision_checker_type=CollisionCheckerType.MESH,use_cuda_graph=False,trajopt_tsteps=20,
            interpolation_dt=1/sim.robot.config.control_hz,collision_cache={'mesh':3,'obb':512},
            collision_activation_distance=.01,fixed_iters_trajopt=True,maximum_trajectory_dt=.5,
            self_collision_check=True,self_collision_opt=True,num_ik_seeds=config.num_ik_seeds,num_trajopt_seeds=config.num_trajopt_seeds)
        self.motion=MotionGen(settings)
        if tuple(self.motion.kinematics.joint_names)!=self.names: raise ValueError('planner released forbidden joints')
        self.motion.warmup(enable_graph=False,warmup_js_trajopt=False)
        self.options=MotionGenPlanConfig(enable_graph=False,max_attempts=config.max_attempts,enable_graph_attempt=None,
            enable_finetune_trajopt=True,parallel_finetune=True,time_dilation_factor=config.time_dilation,
            num_ik_seeds=config.num_ik_seeds,num_trajopt_seeds=config.num_trajopt_seeds,check_start_validity=True)
        self.base=body_pose(sim,sim.model.body('robot_0/base').id)
        fk=self.motion.kinematics.get_state(self.args.to_device(sim.robot.group(side+'_arm')).view(1,-1))
        actual=np.linalg.solve(self.base,tcp_pose(sim,side))
        error=float(np.linalg.norm(fk.ee_position.detach().cpu().numpy().reshape(3)-actual[:3,3]))
        quaternion=fk.ee_quaternion.detach().cpu().numpy().reshape(4)
        rotation=np.zeros(9); mujoco.mju_quat2Mat(rotation,np.ascontiguousarray(quaternion))
        angle=float(np.arccos(np.clip((np.trace(rotation.reshape(3,3).T@actual[:3,:3])-1)/2,-1,1)))
        if error>.001 or angle>.001: raise ValueError(f'cuRobo/MuJoCo FK mismatch {error}m/{angle}rad')
        write_json(path/'fk_check.json',{'translation_error_m':error,'rotation_error_rad':angle})

    def plan(self,goal):
        import time
        from curobo.types.math import Pose
        from curobo.types.robot import JointState
        start=time.monotonic()
        q=self.sim.robot.group(self.side+'_arm')
        state=JointState.from_position(self.args.to_device(q).view(1,-1),list(self.names))
        result=self.motion.plan_single(state,Pose.from_list(pose7(np.linalg.solve(self.base,goal)),self.args),self.options)
        success=result.success is not None and bool(result.success.item())
        points=tuple(tuple(p) for p in result.get_interpolated_plan().position.detach().cpu().numpy().tolist()) if success else ()
        return PlanResult('success' if success and points else 'no_solution',self.names,points,
                          {'status':str(result.status),'wall_time_s':time.monotonic()-start,
                           'max_attempts':self.config.max_attempts,'finite_budget_not_impossibility':True})
