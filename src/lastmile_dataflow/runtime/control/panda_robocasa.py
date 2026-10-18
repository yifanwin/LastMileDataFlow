"""RoboCasa Panda OSC and hybrid-base actions, adapted from robosuite (MIT).

Reference: robosuite OSC_POSE, HYBRID_MOBILE_BASE, PandaGripper and
JOINT_VELOCITY_LEGACY. Uses the project's MjModel/MjData, not a task env.
"""
from copy import deepcopy

import mujoco
import numpy as np

from .action import InvalidAction, vector
from .interface import RobotCommand
from .panda_omron import PandaOmronAdapter


def rotation_vector(matrix):
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.asarray(matrix).reshape(9))
    if quat[0] < 0:
        quat *= -1
    norm = np.linalg.norm(quat[1:])
    return quat[1:] * (2*np.arctan2(norm, quat[0])/norm) if norm > 1e-12 else np.zeros(3)


def rotation_matrix(vector):
    angle = np.linalg.norm(vector)
    if angle < 1e-12:
        return np.eye(3)
    quat, result = np.zeros(4), np.zeros(9)
    mujoco.mju_axisAngle2Quat(quat, vector/angle, angle)
    mujoco.mju_quat2Mat(result, quat)
    return result.reshape(3, 3)


def xyzw(matrix):
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.asarray(matrix).reshape(9))
    return quat[[1, 2, 3, 0]]


def prepare_spec(spec, config):
    """Keep factory motor/velocity actuators and joint friction intact."""
    ns = config.namespace
    support = spec.body(ns+'mobilebase0_support')
    for name, pos, quat in (
        ('robot0_agentview_left', [-.5,.35,1.05], [.55623853,.29935253,-.37678665,-.6775092]),
        ('robot0_agentview_right', [-.5,-.35,1.05], [.67750918865,.37678667903,-.29935255647,-.55623859167]),
    ):
        if spec.camera(ns+name) is None:
            support.add_camera(name=ns+name, pos=pos, quat=quat, fovy=60.)
    # OSC and torso controllers explicitly compensate bias forces.
    for body in spec.bodies:
        if body.name.startswith(ns):
            body.gravcomp = 0.
            for geom in body.geoms:
                if geom.group == 0:
                    geom.material = ''
                    geom.rgba = [0,0,0,0]
    for site in spec.sites:
        if site.name.startswith(ns):
            site.rgba[3] = 0.


class PandaRoboCasaAdapter(PandaOmronAdapter):
    def __init__(self, model, data, config):
        super().__init__(model, data, config)
        ns = config.namespace
        self.origin_id = model.site(ns+'robot0_right_center').id
        self.base_center_id = model.site(ns+'mobilebase0_center').id
        self.hand_id = model.body(ns+'robot0_right_hand').id
        self.dofs = {key: np.array([model.jnt_dofadr[self.joints[name]] for name in names])
                     for key, names in self.groups.items()}
        self.control = None
        self.scratch = mujoco.MjData(model)

    def initialize(self, initial=None):
        if self.execution_started:
            raise RuntimeError('initialization prohibited after begin')
        initial = self.config.initial if initial is None else initial
        values = {'base': initial['base_xyyaw'], 'lift': [initial['lift']],
                  'arm': initial['arm_q']['right'],
                  'gripper': [initial['gripper_width']['right']/2, -initial['gripper_width']['right']/2]}
        for group, qs in values.items():
            for name, q in zip(self.groups[group], qs):
                self.data.qpos[self.addresses[name]] = q
        self.data.qvel[:] = 0
        mujoco.mj_forward(self.model, self.data)
        position, rotation = self.relative_tcp(self.data)
        self.lift_target = float(initial['lift'])
        self.control = {'position': position, 'rotation': rotation,
                        'initial_joint': self.arm_positions('right').copy(),
                        'initial_yaw': float(self.base_pose()[2]), 'base_velocity': np.zeros(3),
                        'height': self.lift_target, 'grip': np.array([1.,-1.]) * initial['gripper_width']['right']/.08,
                        'action': np.r_[np.zeros(6), 0., np.zeros(4), 1.]}
        self.update_control()

    def relative_tcp(self, data):
        origin = data.site_xpos[self.origin_id]
        rotation = data.site_xmat[self.origin_id].reshape(3,3)
        tcp = self.tcp_site('right')
        return rotation.T @ (data.site_xpos[tcp]-origin), rotation.T @ data.site_xmat[tcp].reshape(3,3)

    def snapshot_control(self):
        return {key: value.tolist() if isinstance(value, np.ndarray) else value for key,value in self.control.items()}

    def restore_control(self, saved):
        self.control = {key: np.asarray(value,float) if isinstance(value,list) else value for key,value in saved.items()}
        self.lift_target = float(self.control['height'])

    def copy_control_from(self, other):
        self.control = deepcopy(other.control)
        self.lift_target = other.lift_target

    def hold(self):
        self.update_control()

    def control_height(self):
        return self.lift_target

    def execution_action(self, command):
        if not isinstance(command, RobotCommand):
            return command
        self.scratch.qpos[:] = self.data.qpos
        desired_base = self.base_pose() if command.base_xyyaw is None else np.asarray(command.base_xyyaw)
        height = self.lift_target if command.lift is None else command.lift
        groups = {'base': desired_base, 'lift': [height], 'arm': command.arm_q.get('right', self.arm_positions('right'))}
        for group, qs in groups.items():
            for name,q in zip(self.groups[group],qs):
                self.scratch.qpos[self.addresses[name]] = q
        mujoco.mj_forward(self.model,self.scratch)
        position,rotation = self.relative_tcp(self.scratch)
        delta = desired_base-self.base_pose()
        delta[2] = np.arctan2(np.sin(delta[2]),np.cos(delta[2]))
        velocity = delta*self.config.control_hz
        # Factory velocity actuators have a Coulomb deadband (0.25 m/s
        # for translation). Reference tracking compensates through actions;
        # policy actions and the shared controller retain factory semantics.
        ids = [self.act_ids[name] for name in self.actuators['base']]
        deadband = self.model.dof_frictionloss[self.dofs['base']] / self.model.actuator_gainprm[ids,0]
        velocity = np.where(np.abs(delta)>.001, velocity+np.sign(velocity)*deadband, 0.)
        theta = self.base_pose()[2]-self.control['initial_yaw']
        c,s = np.cos(theta),np.sin(theta)
        base = np.array([[c,s,0],[-s,c,0],[0,0,1.]]) @ velocity / np.array([1.,1.,1.5])
        grip = self.control['action'][6]
        if 'right' in command.gripper_width:
            grip = float(command.gripper_width['right'] < .04)
        return np.r_[(position-self.control['position'])/.05,
                     rotation_vector(rotation @ self.control['rotation'].T)/.5,
                     grip, base, (height-self.group('lift')[0])/.05, 1.]

    def apply(self, action, *, fixed_base=False):
        action = self.execution_action(action)
        a = vector(action,12)
        if fixed_base and np.any(a[7:10] != 0):
            raise InvalidAction('fixed-base trial prohibits base motion')
        a = np.clip(a,-1.,1.)
        a[6],a[11] = float(a[6]>=.5),float(a[11]>=.5)
        position,rotation = self.relative_tcp(self.data)
        if a[11] < .5:
            self.control['position'],self.control['rotation'] = position,rotation
        self.control['position'] = self.control['position'] + .05*a[:3]
        self.control['rotation'] = rotation_matrix(.5*a[3:6]) @ self.control['rotation']
        theta = self.base_pose()[2]-self.control['initial_yaw']
        c,s = np.cos(theta),np.sin(theta)
        self.control['base_velocity'] = np.array([[c,-s,0],[s,c,0],[0,0,1.]]) @ a[7:10] * np.array([1.,1.,1.5])
        self.control['height'] = float(np.clip(self.group('lift')[0]+.05*a[10],0.,.34))
        self.lift_target = self.control['height']
        sign = 1. if a[6] else -1.
        self.control['grip'] = np.clip(self.control['grip'] + np.array([-1.,1.])*.2*sign,-1.,1.)
        self.control['action'] = a.copy()
        self.update_control()
        return {'submitted_action': a.tolist(), 'ctrl': self.data.ctrl.tolist()}

    def update_control(self):
        if self.control is None:
            return
        model,data = self.model,self.data
        mujoco.mj_forward(model,data)
        full_mass = np.zeros((model.nv,model.nv))
        mujoco.mj_fullM(model,full_mass,data.qM)
        jp,jr,cp,cr = [np.zeros((3,model.nv)) for _ in range(4)]
        tcp = self.tcp_site('right')
        mujoco.mj_jacSite(model,data,jp,jr,tcp)
        mujoco.mj_jacSite(model,data,cp,cr,self.origin_id)
        dofs = self.dofs['arm']
        mass = full_mass[np.ix_(dofs,dofs)]
        inv = np.linalg.inv(mass)
        jpos,jori = jp[:,dofs],jr[:,dofs]
        jac = np.vstack([jpos,jori])
        origin = data.site_xpos[self.origin_id]
        origin_rotation = data.site_xmat[self.origin_id].reshape(3,3)
        goal_position = origin + origin_rotation @ self.control['position']
        goal_rotation = origin_rotation @ self.control['rotation']
        current_rotation = data.site_xmat[tcp].reshape(3,3)
        error_rotation = .5*sum((np.cross(current_rotation[:,i],goal_rotation[:,i]) for i in range(3)))
        force = 150*(goal_position-data.site_xpos[tcp])-2*np.sqrt(150)*((jp-cp)@data.qvel)
        torque = 150*error_rotation-2*np.sqrt(150)*((jr-cr)@data.qvel)
        lp = np.linalg.pinv(jpos@inv@jpos.T)
        lr = np.linalg.pinv(jori@inv@jori.T)
        lf = np.linalg.pinv(jac@inv@jac.T)
        nullspace = np.eye(7)-inv@jac.T@lf@jac
        qvel = data.qvel[dofs]
        null_torque = nullspace.T@mass@(10*(self.control['initial_joint']-self.arm_positions('right'))-2*np.sqrt(10)*qvel)
        arm_ctrl = jac.T@np.r_[lp@force,lr@torque]+data.qfrc_bias[dofs]+null_torque
        lift_dof = self.dofs['lift'][0]
        lift_ctrl = full_mass[lift_dof,lift_dof]*(2000*(self.control['height']-self.group('lift')[0])-2*np.sqrt(2000)*data.qvel[lift_dof])+data.qfrc_bias[lift_dof]
        grip = self.control['grip']
        values = {'base': self.control['base_velocity'], 'arm': arm_ctrl, 'lift': [lift_ctrl],
                  'gripper': [.02+.02*grip[0],-.02+.02*grip[1]]}
        for group,commands in values.items():
            for name,value in zip(self.actuators[group],commands):
                index = self.act_ids[name]
                if model.actuator_ctrllimited[index]:
                    value = np.clip(value,*model.actuator_ctrlrange[index])
                data.ctrl[index] = value

    def record_layout(self):
        return {'robot': self.config.name, 'state_key': 'state', 'state_size':16, 'action_size':12,
                'state_layout':'robocasa-state16-v1', 'action_layout':'robocasa-action12-v1',
                'action_convention':'scaled eef delta / gripper close / base velocity / torso delta / mode'}

    def record_state(self):
        data = self.data
        origin = data.site_xpos[self.base_center_id]
        rotation = data.site_xmat[self.base_center_id].reshape(3,3)
        tcp = data.site_xpos[self.tcp_site('right')]
        hand = data.xmat[self.hand_id].reshape(3,3)
        return np.r_[rotation.T@(tcp-origin),xyzw(rotation.T@hand),origin,xyzw(rotation),self.group('gripper')]

    @staticmethod
    def record_action(command):
        return np.asarray(command,float)
