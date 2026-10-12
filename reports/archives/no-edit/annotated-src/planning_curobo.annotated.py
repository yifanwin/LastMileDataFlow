# 注释镜像：仅阅读，不是运行入口。原始源码保持不变，避免破坏正在运行任务的冻结哈希。
"""Native cuRobo: one arm, optionally joint-planned holonomic base in a fixed measured frame."""
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


def locked_arm_config(template,sim,side,root, *, mobile_base=False):
    cfg=copy.deepcopy(template); k=cfg['robot_cfg']['kinematics']; idle='right' if side=='left' else 'left'
    active=(['base_x','base_y','base_theta'] if mobile_base else [])+[f'{side}_arm_{i}' for i in range(7)]
    locks=dict(k.get('lock_joints') or {})
    # TRACE P01：底盘和单臂活动，但躯干、另一臂、head、夹爪均锁在该候选的姿态。放开base不等于全身自由IK。
    for group in ('torso','head',idle+'_arm','left_gripper','right_gripper'):
        locks.update({n:float(q) for n,q in zip(sim.robot.groups[group],sim.robot.group(group))})
    locks.update(dict.fromkeys(('base_x','base_y','base_theta'),0.))
    for n in active: locks.pop(n,None)
    k.update(lock_joints=locks,ee_link=f'ee_{side}_tcp',urdf_path=str(root/'urdf/model_holobase.urdf'),
             asset_root_path=str(root/'urdf/meshes'),collision_spheres=str(root/'rby1m_holobase_spheres.yml'),usd_robot_root=str(root))
    # TRACE P07：注册head及双TCP本身是FK需求，不应等价于固定其世界姿态。旧cuRobo warmup会给这些link建立额外pose目标。
    k['link_names']=list(dict.fromkeys(k.get('link_names',[])+['link_head_2']))
    original=k['cspace']['joint_names']; ids=[original.index(n) for n in active]
    for n,v in k['cspace'].items():
        if isinstance(v,list) and len(v)==len(original): k['cspace'][n]=[v[i] for i in ids]
    k['cspace']['joint_names']=active
    return cfg


def collision_world(sim,path,range_m=2., *, reference_base=None):
    """Per-geom collision mesh, never use furniture-wide AABB as exact geometry."""
    import trimesh
    from curobo.geom.types import WorldConfig,Mesh
    m,d=sim.model,sim.data
    base=body_pose(sim,m.body('robot_0/base').id) if reference_base is None else reference_base
    inv=np.linalg.inv(base)
    target=descendants(m,sim.target_id); vertices=[]; faces=[]; names=[]; count=0
    for g in range(m.ngeom):
        b=int(m.geom_bodyid[g]); name=m.geom(g).name
        # TRACE P02：目标整物体从规划碰撞世界排除，其他家具仍保留；目标非指碰撞由实际物理检查拒绝。
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
    if not vertices:
        write_json(path/'collision_world.json',{'frame':'measured base','geoms':[],
                   'target_excluded':'intended finger contact, checked in physics'})
        return WorldConfig()
    write_json(path/'collision_world.json',{'frame':'measured base','geoms':names,'vertices':count,
                 'target_excluded':'intended finger contact, checked in physics','floor_excluded':'only planner; physics unchanged',
                 'carried_object':'not attached in planner; all target/environment contacts checked in physical execution',
                 'primitive_note':'box/mesh exact; curved primitives tessellated'})
    return WorldConfig(mesh=[Mesh(name='environment',pose=[0,0,0,1,0,0,0],vertices=np.concatenate(vertices).tolist(),faces=np.concatenate(faces).tolist())])


class NativePlanner:
    def __init__(self,sim,config,side,path, *, mobile_base=False,workspace=None):
        import torch
        import yaml
        from curobo.geom.sdf.world import CollisionCheckerType
        from curobo.types.base import TensorDeviceType
        from curobo.wrap.reacher.motion_gen import MotionGen,MotionGenConfig,MotionGenPlanConfig
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; no CPU substitute for cuRobo')
        torch.manual_seed(config.seed)
        self.sim,self.config,self.side=sim,config,side; self.args=TensorDeviceType()
        root=Path(config.robot_planner_dir); path=Path(path)
        self.mobile_base=mobile_base
        self.base=body_pose(sim,sim.model.body('robot_0/base').id)
        self.world_range=2.
        cfg=locked_arm_config(yaml.safe_load((root/'rby1m_holobase.yml').read_text()),sim,side,root,
                              mobile_base=mobile_base)
        if mobile_base:
            if workspace is None: raise ValueError('mobile planning requires explicit workspace')
            center,radius=workspace
            # A generated local URDF bounds the virtual joints. Original assets
            # remain read-only. The exact circular workspace is checked in physics.
            import xml.etree.ElementTree as ET
            tree=ET.parse(root/'urdf/model_holobase.urdf')
            local=np.linalg.solve(self.base,np.r_[center[:2],self.base[2,3],1.])[:2]
            bounds={'base_x':(local[0]-radius,local[0]+radius),
                    'base_y':(local[1]-radius,local[1]+radius),'base_theta':(-np.pi,np.pi)}
            for name,(lo,hi) in bounds.items():
                joint=tree.getroot().find(f"joint[@name='{name}']")
                joint.set('type','revolute' if name=='base_theta' else 'prismatic')
                joint.find('limit').set('lower',str(lo)); joint.find('limit').set('upper',str(hi))
            generated=path/'mobile_holobase.urdf'; tree.write(generated,encoding='utf-8',xml_declaration=True)
            cfg['robot_cfg']['kinematics']['urdf_path']=str(generated.resolve())
            self.world_range=2*radius+1.
            write_json(path/'mobile_base.json',{'active_joints':list(bounds),'reference_world':self.base.tolist(),
                'joint_bounds_local':bounds,'workspace_center_world':list(center[:2]),'workspace_radius_m':radius,
                'exact_circle_check':'every executed physics tick','collision_range_m':self.world_range})
        self.robot_cfg=cfg['robot_cfg']
        (path/'planner.yml').write_text(yaml.safe_dump(cfg,sort_keys=False))
        write_json(path/'planner_assets.json',{str(p):file_digest(p) for p in (root/'rby1m_holobase.yml',root/'rby1m_holobase_spheres.yml',root/'urdf/model_holobase.urdf')})
        self.names=tuple((['base_x','base_y','base_theta'] if mobile_base else [])+[f'{side}_arm_{i}' for i in range(7)])
        settings=MotionGenConfig.load_from_robot_config(cfg['robot_cfg'],collision_world(sim,path,self.world_range,
            reference_base=self.base),self.args,
            collision_checker_type=CollisionCheckerType.MESH,use_cuda_graph=False,trajopt_tsteps=20,
            interpolation_dt=1/sim.robot.config.control_hz,collision_cache={'mesh':3,'obb':512},
            # TRACE P03：使用机器人碰撞球、5mm sphere buffer与1cm避碰代价激活距离；不等价于MuJoCo精确几何。激活距离不是简单1cm硬拒绝阈值。
            collision_activation_distance=.01,fixed_iters_trajopt=True,maximum_trajectory_dt=.5,
            self_collision_check=True,self_collision_opt=True,num_ik_seeds=config.num_ik_seeds,num_trajopt_seeds=config.num_trajopt_seeds)
        # TRACE P04：未显式把task seed传给MotionGen的ik_seed/trajopt_seed，本机cuRobo使用固定默认值；五次试验不保证五套IK随机种子。
        self.motion=MotionGen(settings)
        if tuple(self.motion.kinematics.joint_names)!=self.names: raise ValueError('planner released forbidden joints')
        # TRACE P08：本机旧cuRobo warmup仍调用plan_single(link_poses=state.link_pose)。后续None不会清空缓存，head/闲置TCP的世界pose代价限制底盘运动。S0042仅抑制这些warmup目标、保留关节锁和全部避碰后，两段规划均成功。
        self.motion.warmup(enable_graph=False,warmup_js_trajopt=False)
        self.options=MotionGenPlanConfig(enable_graph=False,max_attempts=config.max_attempts,enable_graph_attempt=None,
            enable_finetune_trajopt=True,parallel_finetune=True,time_dilation_factor=config.time_dilation,
            num_ik_seeds=config.num_ik_seeds,num_trajopt_seeds=config.num_trajopt_seeds,check_start_validity=True)
        fk=self.motion.kinematics.get_state(self.args.to_device(self.current_joints()).view(1,-1))
        actual=np.linalg.solve(self.base,tcp_pose(sim,side))
        error=float(np.linalg.norm(fk.ee_position.detach().cpu().numpy().reshape(3)-actual[:3,3]))
        quaternion=fk.ee_quaternion.detach().cpu().numpy().reshape(4)
        rotation=np.zeros(9); mujoco.mju_quat2Mat(rotation,np.ascontiguousarray(quaternion))
        angle=float(np.arccos(np.clip((np.trace(rotation.reshape(3,3).T@actual[:3,:3])-1)/2,-1,1)))
        if error>.001 or angle>.001: raise ValueError(f'cuRobo/MuJoCo FK mismatch {error}m/{angle}rad')
        write_json(path/'fk_check.json',{'translation_error_m':error,'rotation_error_rad':angle})

    def current_joints(self):
        arm=self.sim.robot.group(self.side+'_arm')
        if not self.mobile_base: return arm
        relative=np.linalg.solve(self.base,body_pose(self.sim,self.sim.model.body('robot_0/base').id))
        return np.r_[relative[:2,3],np.arctan2(relative[1,0],relative[0,0]),arm]

    def base_target_world(self,point):
        if not self.mobile_base: raise ValueError('not a mobile-base plan')
        x,y,yaw=np.asarray(point)[:3]
        local=np.eye(4); local[:2,3]=[x,y]
        local[:2,:2]=[[np.cos(yaw),-np.sin(yaw)],[np.sin(yaw),np.cos(yaw)]]
        world=self.base@local
        return np.r_[world[:2,3],np.arctan2(world[1,0],world[0,0])]

    def free_ik_diagnostic(self,goal):
        """Bounded joint-limited IK without world/self collision, diagnostic ONLY."""
        from curobo.wrap.reacher.ik_solver import IKSolver,IKSolverConfig
        from curobo.types.math import Pose
        # TRACE P05：无环境且关闭自碰撞，只能做有限种子kinematic可解性诊断；true不能直接证明原失败是某个几何碰撞。
        settings=IKSolverConfig.load_from_robot_config(self.robot_cfg,world_model=None,
            tensor_args=self.args,num_seeds=self.config.num_ik_seeds,use_cuda_graph=False,
            self_collision_check=False,self_collision_opt=False,seed=self.config.seed)
        solver=IKSolver(settings)
        result=solver.solve_single(Pose.from_list(pose7(np.linalg.solve(self.base,goal)),self.args))
        return {'free_ik_success':bool(result.success.any().item()),
                'scope':'finite candidate/seed budget, not proof of global unreachability',
                'num_ik_seeds':self.config.num_ik_seeds}

    def attach_target_bbox(self):
        """Conservative 27-sphere carried-object approximation, planner only."""
        import itertools
        from ..scenes.geometry import body_points
        points=body_points(self.sim,self.sim.target_id)
        tcp=tcp_pose(self.sim,self.side)
        local=(points-tcp[:3,3])@tcp[:3,:3]
        lo,hi=local.min(axis=0),local.max(axis=0)
        step=(hi-lo)/3; radius=float(np.linalg.norm(step)/2+.001)
        centers=np.array([lo+(np.array(index)+.5)*step for index in itertools.product(range(3),repeat=3)])
        link='attached_object_'+self.side
        count=self.motion.robot_cfg.kinematics.kinematics_config.get_number_of_spheres(link)
        if count < len(centers): raise ValueError('insufficient attached object spheres')
        spheres=np.zeros((count,4)); spheres[:,3]=-10.
        spheres[:len(centers),:3]=centers; spheres[:len(centers),3]=radius
        self.motion.attach_spheres_to_robot(sphere_tensor=self.args.to_device(spheres),link_name=link)
        return {'method':'27 enclosing spheres of measured TCP-frame bbox',
                'link':link,'min':lo.tolist(),'max':hi.tolist(),'radius_m':radius,
                'physics_attachment':False}

    def refresh_world(self,path):
        self.motion.update_world(collision_world(self.sim,Path(path),self.world_range,reference_base=self.base))

    def plan(self,goal):
        import time
        from curobo.types.math import Pose
        from curobo.types.robot import JointState
        start=time.monotonic()
        q=self.current_joints()
        state=JointState.from_position(self.args.to_device(q).view(1,-1),list(self.names))
        # TRACE P06：MotionGen以当前实测关节作为start，还传入其内部IK seed/retract策略；内部IK_FAIL可来自优化搜索不收敛，不是不可达证书。
        result=self.motion.plan_single(state,Pose.from_list(pose7(np.linalg.solve(self.base,goal)),self.args),self.options)
        success=result.success is not None and bool(result.success.item())
        points=tuple(tuple(p) for p in result.get_interpolated_plan().position.detach().cpu().numpy().tolist()) if success else ()
        return PlanResult('success' if success and points else 'no_solution',self.names,points,
                          {'status':str(result.status),'wall_time_s':time.monotonic()-start,
                           'max_attempts':self.config.max_attempts,'finite_budget_not_impossibility':True})
