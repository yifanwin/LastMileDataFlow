"""Isolated RBY-1 gripper close/lift/shake simulation. Not strict task execution.

Only this runtime module steps the isolated model. Each candidate gets fresh MjData.
The isolated gripper copies the native finger dynamics, single-actuator coupling,
contact parameters and timestep; nothing is tuned here to make grasps pass.
"""
import xml.etree.ElementTree as ET
import numpy as np
import mujoco
from ..io import digest
from ..grasping.geometry import geom_mesh
from ..planning.curobo import pose7

FILTER_VERSION = 'isolated-close-lift-shake-v2'
# Stand-in for the arm behind the wrist: heavy enough that finger reaction forces barely move the hand.
HAND_MASS_KG = 2.
HAND_INERTIA = .01
WELD_TIMECONST_S = .01


def extract_gripper(sim,side='left'):
    if sim.started or sim.closed: raise RuntimeError('gripper extraction requires preparation')
    m=sim.model
    # A separate MjData isolates model geometry extraction from the source scene state.
    data=mujoco.MjData(m); data.qpos[:]=sim.data.qpos
    joints={}
    for suffix in (1,2):
        j=m.joint(f'robot_0/gripper_finger_{side[0]}{suffix}').id
        joints[j]='finger'+str(suffix)
        data.qpos[m.jnt_qposadr[j]]=0.
    mujoco.mj_forward(m,data)
    from types import SimpleNamespace
    view=SimpleNamespace(model=m,data=data); site=m.site(f'robot_0/ee_site_{side[0]}').id
    frame=np.eye(4); frame[:3,:3]=data.site_xmat[site].reshape(3,3); frame[:3,3]=data.site_xpos[site]
    groups=[]
    for name,suffix in ((f'robot_0/EE_BODY_{side[0].upper()}',None),(f'robot_0/ee_finger_{side[0]}1',1),(f'robot_0/ee_finger_{side[0]}2',2)):
        body=m.body(name).id
        meshes=[geom_mesh(view,g,frame) for g in range(m.ngeom) if m.geom_bodyid[g]==body and (m.geom_contype[g] or m.geom_conaffinity[g])]
        group={'name':'palm' if suffix is None else 'finger'+str(suffix),'meshes':meshes}
        if suffix is not None:
            j=m.joint(f'robot_0/gripper_finger_{side[0]}{suffix}').id; dof=m.jnt_dofadr[j]
            axis=data.xmat[body].reshape(3,3)@m.jnt_axis[j]
            rotation=data.xmat[body].reshape(3,3); inertial=np.zeros(4)
            mujoco.mju_mulQuat(inertial,data.xquat[body],m.body_iquat[body])
            inertial_rotation=np.zeros(9); mujoco.mju_quat2Mat(inertial_rotation,inertial)
            inertial_frame=frame[:3,:3].T@inertial_rotation.reshape(3,3); q=np.zeros(4)
            mujoco.mju_mat2Quat(q,np.ascontiguousarray(inertial_frame).reshape(-1))
            group.update(axis=(frame[:3,:3].T@axis).tolist(),range=m.jnt_range[j].tolist(),
                         damping=float(m.dof_damping[dof]),armature=float(m.dof_armature[dof]),
                         frictionloss=float(m.dof_frictionloss[dof]),gravcomp=float(m.body_gravcomp[body]),
                         mass=float(m.body_mass[body]),inertia=m.body_inertia[body].tolist(),
                         com=((data.xpos[body]+rotation@m.body_ipos[body]-frame[:3,3])@frame[:3,:3]).tolist(),
                         inertia_quat=q.tolist())
        groups.append(group)
    if any(not g['meshes'] for g in groups): raise ValueError('native palm/finger geometry missing')
    act=m.actuator(f'robot_0/{side}_finger_act').id
    if m.actuator_trntype[act]!=mujoco.mjtTrn.mjTRN_JOINT or int(m.actuator_trnid[act,0]) not in joints:
        raise ValueError('native gripper actuator must drive one finger joint')
    coupling=None
    for e in range(m.neq):
        if m.eq_type[e]==mujoco.mjtEq.mjEQ_JOINT and {int(m.eq_obj1id[e]),int(m.eq_obj2id[e])}==set(joints):
            coupling={'joint1':joints[int(m.eq_obj1id[e])],'joint2':joints[int(m.eq_obj2id[e])],
                      'polycoef':m.eq_data[e][:5].tolist(),'solref':m.eq_solref[e].tolist(),'solimp':m.eq_solimp[e].tolist()}
    if coupling is None: raise ValueError('native finger coupling equality missing')
    lo,hi=m.actuator_ctrlrange[act]
    actuator={'joint':joints[int(m.actuator_trnid[act,0])],'kp':float(m.actuator_gainprm[act,0]),
              'kv':float(-m.actuator_biasprm[act,2]),'ctrlrange':[float(lo),float(hi)],
              'forcerange':m.actuator_forcerange[act].tolist() if m.actuator_forcelimited[act] else None}
    options={'timestep':float(m.opt.timestep),'cone':int(m.opt.cone),'impratio':float(m.opt.impratio),
             'integrator':int(m.opt.integrator),'iterations':int(m.opt.iterations),'noslip_iterations':int(m.opt.noslip_iterations),
             'multiccd':bool(m.opt.enableflags & mujoco.mjtEnableBit.mjENBL_MULTICCD)}
    return {'groups':groups,'actuator':actuator,'coupling':coupling,'options':options,
            # Palm is welded to the fingers' parent link natively, so parent filtering removes palm-finger contacts.
            'excluded_pairs':[['palm','finger1'],['palm','finger2']]}


def subtree_inertial(model,body):
    """Composite mass/inertia of a target subtree in the target body frame (assets often nest geoms one body down)."""
    data=mujoco.MjData(model); mujoco.mj_forward(model,data)
    bodies=[b for b in range(model.nbody) if b==body or _ancestor(model,b,body)]
    rotation=data.xmat[body].reshape(3,3); origin=data.xpos[body]
    mass=sum(float(model.body_mass[b]) for b in bodies)
    if mass<=0: raise ValueError('target subtree has no mass')
    com=sum(float(model.body_mass[b])*data.xipos[b] for b in bodies)/mass
    tensor=np.zeros((3,3))
    for b in bodies:
        r=data.ximat[b].reshape(3,3); local=r@np.diag(model.body_inertia[b])@r.T; d=data.xipos[b]-com
        tensor+=local+float(model.body_mass[b])*(np.dot(d,d)*np.eye(3)-np.outer(d,d))
    tensor=rotation.T@tensor@rotation; values,vectors=np.linalg.eigh(tensor)
    if np.linalg.det(vectors)<0: vectors[:,0]*=-1
    q=np.zeros(4); mujoco.mju_mat2Quat(q,np.ascontiguousarray(vectors).reshape(-1))
    return {'mass':mass,'pos':(rotation.T@(com-origin)).tolist(),'quat':q.tolist(),'inertia':np.maximum(values,1e-9).tolist()}


def _ancestor(model,b,ancestor):
    while b:
        b=int(model.body_parentid[b])
        if b==ancestor: return True
    return False


def _numbers(x): return ' '.join(str(float(v)) for v in np.asarray(x).reshape(-1))


def _contact_attributes(mesh):
    c=mesh.get('contact')
    if c is None: return {'friction':'1 .005 .0001','condim':'3'}
    return {'friction':_numbers(c['friction']),'condim':str(c['condim']),'solref':_numbers(c['solref']),
            'solimp':_numbers(c['solimp']),'margin':str(c['margin']),'gap':str(c['gap'])}


def isolated_model(gripper,object_meshes,mass,object_inertial=None):
    o=gripper['options']
    xml=ET.Element('mujoco',model='rby1_isolated_grasp_filter'); ET.SubElement(xml,'compiler',angle='radian',inertiafromgeom='auto')
    ET.SubElement(xml,'option',timestep=str(o['timestep']),gravity='0 0 -9.81',iterations=str(o['iterations']),
                  impratio=str(o['impratio']),noslip_iterations=str(o['noslip_iterations']),
                  cone=('pyramidal','elliptic')[o['cone']],integrator=('Euler','RK4','implicit','implicitfast')[o['integrator']])
    if o.get('multiccd'): ET.SubElement(xml.find('option'),'flag',multiccd='enable')
    asset=ET.SubElement(xml,'asset'); world=ET.SubElement(xml,'worldbody'); ET.SubElement(world,'geom',name='floor',type='plane',size='1 1 .02')
    obj=ET.SubElement(world,'body',name='object'); ET.SubElement(obj,'freejoint',name='object_free')
    if object_inertial is not None:
        ET.SubElement(obj,'inertial',pos=_numbers(object_inertial['pos']),quat=_numbers(object_inertial['quat']),
                      mass=str(object_inertial['mass']),diaginertia=_numbers(object_inertial['inertia']))
    # A kinematic mocap target pulls a dynamic hand through a stiff weld. A mocap body itself has no
    # velocity, so fingers parented to it cannot drag an object by friction (the v1 filter failed this way).
    ET.SubElement(world,'body',name='tcp_target',mocap='true')
    root=ET.SubElement(world,'body',name='tcp',gravcomp='1'); ET.SubElement(root,'freejoint',name='hand_free')
    ET.SubElement(root,'inertial',pos='0 0 0',mass=str(HAND_MASS_KG),diaginertia=_numbers([HAND_INERTIA]*3))
    def add_meshes(body,meshes,prefix,total_mass=None):
        for i,mesh in enumerate(meshes):
            name=f'{prefix}_{i}'; ET.SubElement(asset,'mesh',name=name,vertex=_numbers(mesh['vertices']),face=' '.join(str(int(v)) for v in np.array(mesh['faces']).reshape(-1)))
            attributes=dict(name=name,type='mesh',mesh=name,contype='1',conaffinity='1',**_contact_attributes(mesh))
            if total_mass is not None: attributes['mass']=str(total_mass/len(meshes))
            ET.SubElement(body,'geom',**attributes)
    add_meshes(obj,object_meshes,'object',None if object_inertial is not None else mass)
    for group in gripper['groups']:
        body=ET.SubElement(root,'body',name=group['name'])
        if 'axis' in group:
            body.set('gravcomp',str(group['gravcomp']))
            ET.SubElement(body,'inertial',pos=_numbers(group['com']),quat=_numbers(group['inertia_quat']),
                          mass=str(group['mass']),diaginertia=_numbers(group['inertia']))
            ET.SubElement(body,'joint',name=group['name'],type='slide',axis=_numbers(group['axis']),range=_numbers(group['range']),
                          damping=str(group['damping']),armature=str(group['armature']),frictionloss=str(group['frictionloss']))
            add_meshes(body,group['meshes'],group['name'])
        else:
            add_meshes(body,group['meshes'],group['name'],.03)
    a=gripper['actuator']; actuator=ET.SubElement(xml,'actuator')
    attributes=dict(name='gripper',joint=a['joint'],kp=str(a['kp']),kv=str(a['kv']),ctrlrange=_numbers(a['ctrlrange']))
    if a['forcerange'] is not None: attributes['forcerange']=_numbers(a['forcerange'])
    ET.SubElement(actuator,'position',**attributes)
    c=gripper['coupling']; equality=ET.SubElement(xml,'equality')
    ET.SubElement(equality,'weld',body1='tcp_target',body2='tcp',solref=_numbers([max(WELD_TIMECONST_S,2*o['timestep']),1]))
    ET.SubElement(equality,'joint',joint1=c['joint1'],joint2=c['joint2'],polycoef=_numbers(c['polycoef']),
                  solref=_numbers(c['solref']),solimp=_numbers(c['solimp']))
    contact=ET.SubElement(xml,'contact')
    for b1,b2 in gripper['excluded_pairs']: ET.SubElement(contact,'exclude',body1=b1,body2=b2)
    return mujoco.MjModel.from_xml_string(ET.tostring(xml,encoding='unicode'))


def _joint_open_positions(model,gripper,open_command):
    """Joint positions that the native coupling implies for the open command."""
    a,c=gripper['actuator'],gripper['coupling']
    values={a['joint']:open_command}
    p=c['polycoef']
    if c['joint1']==a['joint']:
        if abs(p[1])<1e-9: raise ValueError('degenerate finger coupling')
        values[c['joint2']]=(open_command-p[0])/p[1]
    else:
        values[c['joint1']]=p[0]+p[1]*open_command
    return values


def filter_grasps(gripper,meshes,candidates,*,mass=.1,object_rotation=None,object_inertial=None):
    model=isolated_model(gripper,meshes,mass,object_inertial); result=[]
    dt=model.opt.timestep; steps=lambda seconds: max(1,int(round(seconds/dt)))
    rotation=np.eye(3) if object_rotation is None else np.asarray(object_rotation)
    points=np.concatenate([np.array(m['vertices']) for m in meshes])@rotation.T
    target=np.eye(4); target[:3,:3]=rotation; target[2,3]=.003-points[:,2].min()
    obj=model.body('object').id
    object_adr=model.jnt_qposadr[model.joint('object_free').id]
    hand_adr=model.jnt_qposadr[model.joint('hand_free').id]; hand_dof=model.jnt_dofadr[model.joint('hand_free').id]
    object_geoms={g for g in range(model.ngeom) if model.geom_bodyid[g]==obj}
    finger_geoms={g for g in range(model.ngeom) if model.body(int(model.geom_bodyid[g])).name in ('finger1','finger2')}
    gripper_geoms={g for g in range(model.ngeom) if model.body(int(model.geom_bodyid[g])).name in ('finger1','finger2','palm')}
    open_command,closed_command=gripper['actuator']['ctrlrange']
    open_q=_joint_open_positions(model,gripper,open_command)
    for candidate in candidates:
        data=mujoco.MjData(model); data.qpos[object_adr:object_adr+7]=pose7(target)
        for name,q in open_q.items(): data.qpos[model.jnt_qposadr[model.joint(name).id]]=q
        data.ctrl[0]=open_command
        goal=target@np.array(candidate['transform']); pre=goal.copy(); pre[:3,3]-=.08*goal[:3,2]
        def set_tcp(pose,*,teleport=False):
            q=pose7(pose); data.mocap_pos[0]=q[:3]; data.mocap_quat[0]=q[3:]
            if teleport: data.qpos[hand_adr:hand_adr+7]=q; data.qvel[hand_dof:hand_dof+6]=0
        clear=True; approach_collisions=[]
        # Swept approach, not just endpoint geometry checks.
        for alpha in np.linspace(0,1,21):
            pose=goal.copy(); pose[:3,3]=(1-alpha)*pre[:3,3]+alpha*goal[:3,3]; set_tcp(pose,teleport=True); mujoco.mj_forward(model,data)
            for c in data.contact:
                pair={int(c.geom1),int(c.geom2)}
                if c.dist < -.0005 and pair & gripper_geoms and (pair & object_geoms or model.geom('floor').id in pair):
                    clear=False
                    if len(approach_collisions)<8: approach_collisions.append({'geoms':[model.geom(int(c.geom1)).name,model.geom(int(c.geom2)).name],'distance_m':float(c.dist),'alpha':float(alpha)})
        if not clear:
            result.append(dict(candidate,simulation_pass=False,filter_reason='approach_collision',
                               simulation_evidence={'protocol':FILTER_VERSION,'stage':'approach_geometry','not_task_success':True,'collisions':approach_collisions})); continue
        set_tcp(goal,teleport=True); mujoco.mj_forward(model,data); initial_height=float(data.xpos[obj,2])
        close_steps,settle_steps=steps(1.),steps(.6)
        for step in range(close_steps+settle_steps):
            alpha=min(1.,step/close_steps); data.ctrl[0]=open_command+(closed_command-open_command)*alpha; mujoco.mj_step(model,data)
        lift_steps,hold_steps=steps(1.2),steps(1.2)
        for step in range(lift_steps+hold_steps):
            pose=goal.copy(); pose[2,3]+=.12*min(1.,step/lift_steps)
            if step>lift_steps: pose[0,3]+=.015*np.sin((step-lift_steps)*dt*20)
            set_tcp(pose); mujoco.mj_step(model,data)
        mujoco.mj_forward(model,data)
        contacts=set()
        for contact_id,c in enumerate(data.contact):
            pair={int(c.geom1),int(c.geom2)}
            if pair & object_geoms and pair & finger_geoms:
                f=next(iter(pair & finger_geoms)); force=np.zeros(6); mujoco.mj_contactForce(model,data,contact_id,force)
                if force[0]>.01: contacts.add(model.body(int(model.geom_bodyid[f])).name)
        lift=float(data.xpos[obj,2]-initial_height)
        healthy=all(np.isfinite(v).all() for v in (data.qpos,data.qvel,data.qacc)) and not any(w.number for w in data.warning)
        passed=bool(healthy and lift>.08 and len(contacts)==2)
        evidence={'isolated_model_digest':digest({'gripper':gripper,'meshes':meshes,'mass':mass,'object_inertial':object_inertial}),
                  'lift_m':lift,'bilateral_contacts':len(contacts)==2,'shake_amplitude_m':.015,
                  'simulated_s':float((close_steps+settle_steps+lift_steps+hold_steps)*dt),'timestep_s':float(dt),
                  'protocol':FILTER_VERSION,'not_task_success':True}
        row=dict(candidate,simulation_pass=passed,filter_reason='pass' if passed else 'lift_or_bilateral_contact_failed')
        row['simulation_evidence']={**evidence,'physics_healthy':healthy}
        result.append(row)
    return result
