"""RoboCasa Panda OSC and hybrid-base actions, adapted from robosuite (MIT).

Reference: robosuite OSC_POSE, HYBRID_MOBILE_BASE, PandaGripper and
JOINT_VELOCITY_LEGACY. Uses the project's MjModel/MjData, not a task env.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass

import mujoco
import numpy as np

from .action import InvalidAction, vector
from .interface import RobotCommand
from .panda_omron import SCALE, PandaBody


# RoboCasa CAM_CONFIGS['DEFAULT']; wrist optics come from the Panda factory XML.
# https://github.com/robocasa/robocasa/blob/main/robocasa/utils/camera_utils.py
ROBOCASA_CAMERAS = {
    'robot0_agentview_left': ('mobilebase0_support', [-.5, .35, 1.05],
                            [.55623853, .29935253, -.37678665, -.6775092], 60.),
    'robot0_agentview_right': ('mobilebase0_support', [-.5, -.35, 1.05],
                             [.67750918865, .37678667903, -.29935255647, -.55623859167], 60.),
    'robot0_eye_in_hand': ('robot0_right_hand', [.05, 0., 0.],
                         [0., .707107, .707107, 0.], 75.),
}


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
    for name, (parent, pos, quat, fovy) in ROBOCASA_CAMERAS.items():
        if spec.camera(ns+name) is None:
            spec.body(ns+parent).add_camera(name=ns+name, pos=pos, quat=quat, fovy=fovy)
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


# robosuite OSC_POSE / HYBRID_MOBILE_BASE defaults used by RoboCasa.
OSC_POSITION_SCALE = .05        # metres per unit eef action
OSC_ROTATION_SCALE = .5         # radians per unit eef action
OSC_KP = 150.                   # critically damped eef stiffness
NULLSPACE_KP = 10.              # joint posture stiffness in the OSC nullspace
LIFT_KP = 2000.                 # torso height position gain
LIFT_SCALE = .05                # metres per unit torso action
BASE_YAW_SCALE = 1.5            # base yaw velocity per unit action
GRIP_STEP = .2                  # gripper normalized change per control step
GRIP_CLOSE_WIDTH_M = .04        # semantic widths below this close the gripper
FINGER_CENTER_M = .02           # PandaGripper finger ctrl = centre + centre * normalized grip
BASE_SCALE = np.array([1., 1., BASE_YAW_SCALE])


@dataclass
class OscState:
    """Controller state that survives snapshot/restore; field names are the snapshot keys."""
    position: np.ndarray
    rotation: np.ndarray
    initial_joint: np.ndarray
    initial_yaw: float
    base_velocity: np.ndarray
    height: float
    grip: np.ndarray
    action: np.ndarray

    def to_json(self):
        return {key: value.tolist() if isinstance(value, np.ndarray) else value
                for key, value in asdict(self).items()}

    @classmethod
    def from_json(cls, saved):
        return cls(**{key: np.asarray(value, float) if isinstance(value, list) else value
                      for key, value in saved.items()})


class PandaRoboCasaAdapter(PandaBody):
    """RoboCasa OSC arm, hybrid velocity base and torque-servoed lift."""

    def __init__(self, model, data, config):
        super().__init__(model, data, config)
        ns = config.namespace
        self.origin_id = model.site(ns+'robot0_right_center').id
        self.base_center_id = model.site(ns+'mobilebase0_center').id
        self.hand_id = model.body(ns+'robot0_right_hand').id
        self.control = None
        self.scratch = mujoco.MjData(model)

    def initialize(self, initial=None):
        if self.execution_started:
            raise RuntimeError('initialization prohibited after begin')
        initial = self.initial_state(initial)
        width = initial['gripper_width']['right']
        values = {'base': initial['base_xyyaw'], 'lift': [initial['lift']],
                  'arm': initial['arm_q']['right'], 'gripper': [width/2, -width/2]}
        for group, qs in values.items():
            self.joints.write(self.data, group, qs)
        self.data.qvel[:] = 0
        mujoco.mj_forward(self.model, self.data)
        position, rotation = self.relative_tcp(self.data)
        self.lift_target = float(initial['lift'])
        self.control = OscState(position, rotation, self.arm_positions('right').copy(),
                                float(self.base_pose()[2]), np.zeros(3), self.lift_target,
                                np.array([1., -1.]) * width/self.open_grip,
                                np.r_[np.zeros(6), 0., np.zeros(4), 1.])
        self.update_control()

    def relative_tcp(self, data):
        origin = data.site_xpos[self.origin_id]
        rotation = data.site_xmat[self.origin_id].reshape(3,3)
        tcp = self.tcp_site('right')
        return rotation.T @ (data.site_xpos[tcp]-origin), rotation.T @ data.site_xmat[tcp].reshape(3,3)

    def snapshot_control(self):
        return self.control.to_json()

    def restore_control(self, saved):
        self.control = OscState.from_json(saved)
        self.lift_target = float(self.control.height)

    def copy_control_from(self, other):
        self.control = deepcopy(other.control)
        self.lift_target = other.lift_target

    def hold(self):
        self.update_control()

    def execution_action(self, command):
        if not isinstance(command, RobotCommand):
            return command
        self.scratch.qpos[:] = self.data.qpos
        desired_base = self.base_pose() if command.base_xyyaw is None else np.asarray(command.base_xyyaw)
        height = self.lift_target if command.lift is None else command.lift
        groups = {'base': desired_base, 'lift': [height], 'arm': command.arm_q.get('right', self.arm_positions('right'))}
        for group, qs in groups.items():
            self.joints.write(self.scratch, group, qs)
        mujoco.mj_forward(self.model,self.scratch)
        position,rotation = self.relative_tcp(self.scratch)
        delta = desired_base-self.base_pose()
        delta[2] = np.arctan2(np.sin(delta[2]),np.cos(delta[2]))
        velocity = delta*self.config.control_hz
        # Factory velocity actuators have a Coulomb deadband (0.25 m/s
        # for translation). Reference tracking compensates through actions;
        # policy actions and the shared controller retain factory semantics.
        ids = self.joints.actuator_ids('base')
        deadband = self.model.dof_frictionloss[self.joints.group_dofs('base')] / self.model.actuator_gainprm[ids, 0]
        velocity = np.where(np.abs(delta) > .001, velocity + np.sign(velocity)*deadband, 0.)
        theta = self.base_pose()[2] - self.control.initial_yaw
        c, s = np.cos(theta), np.sin(theta)
        base = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1.]]) @ velocity / BASE_SCALE
        grip = self.control.action[6]
        if 'right' in command.gripper_width:
            grip = float(command.gripper_width['right'] < GRIP_CLOSE_WIDTH_M)
        return np.r_[(position - self.control.position)/OSC_POSITION_SCALE,
                     rotation_vector(rotation @ self.control.rotation.T)/OSC_ROTATION_SCALE,
                     grip, base, (height - self.group('lift')[0])/LIFT_SCALE, 1.]

    def apply(self, action, *, fixed_base=False):
        action = self.execution_action(action)
        a = vector(action,12)
        if fixed_base and np.any(a[7:10] != 0):
            raise InvalidAction('fixed-base trial prohibits base motion')
        a = np.clip(a,-1.,1.)
        a[6],a[11] = float(a[6]>=.5),float(a[11]>=.5)
        control = self.control
        position, rotation = self.relative_tcp(self.data)
        if a[11] < .5:
            control.position, control.rotation = position, rotation
        control.position = control.position + OSC_POSITION_SCALE*a[:3]
        control.rotation = rotation_matrix(OSC_ROTATION_SCALE*a[3:6]) @ control.rotation
        theta = self.base_pose()[2] - control.initial_yaw
        c, s = np.cos(theta), np.sin(theta)
        control.base_velocity = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.]]) @ a[7:10] * BASE_SCALE
        lift_max = self.model.jnt_range[self.joints.ids[self.joints.groups['lift'][0]], 1]
        control.height = float(np.clip(self.group('lift')[0] + LIFT_SCALE*a[10], 0., lift_max))
        self.lift_target = control.height
        sign = 1. if a[6] else -1.
        control.grip = np.clip(control.grip + np.array([-1., 1.])*GRIP_STEP*sign, -1., 1.)
        control.action = a.copy()
        self.update_control()
        return {'submitted_action': a.tolist(), 'ctrl': self.data.ctrl.tolist()}

    def update_control(self):
        if self.control is None:
            return
        model, data = self.model, self.data
        mujoco.mj_forward(model, data)
        full_mass = np.zeros((model.nv, model.nv))
        mujoco.mj_fullM(model, data, full_mass)
        values = {'base': self.control.base_velocity, 'arm': self._arm_torque(full_mass),
                  'lift': [self._lift_force(full_mass)],
                  'gripper': FINGER_CENTER_M*np.array([1., -1.]) + FINGER_CENTER_M*self.control.grip}
        for group, commands in values.items():
            for name, value in zip(self.joints.actuators[group], commands):
                index = self.joints.act_ids[name]
                if model.actuator_ctrllimited[index]:
                    value = np.clip(value, *model.actuator_ctrlrange[index])
                data.ctrl[index] = value

    def _arm_torque(self, full_mass):
        """robosuite OSC_POSE with nullspace posture control and bias compensation."""
        model, data = self.model, self.data
        jp, jr, cp, cr = [np.zeros((3, model.nv)) for _ in range(4)]
        tcp = self.tcp_site('right')
        mujoco.mj_jacSite(model, data, jp, jr, tcp)
        mujoco.mj_jacSite(model, data, cp, cr, self.origin_id)
        dofs = self.joints.group_dofs('arm')
        mass = full_mass[np.ix_(dofs, dofs)]
        inv = np.linalg.inv(mass)
        jpos, jori = jp[:, dofs], jr[:, dofs]
        jac = np.vstack([jpos, jori])
        origin = data.site_xpos[self.origin_id]
        origin_rotation = data.site_xmat[self.origin_id].reshape(3, 3)
        goal_position = origin + origin_rotation @ self.control.position
        goal_rotation = origin_rotation @ self.control.rotation
        current_rotation = data.site_xmat[tcp].reshape(3, 3)
        error_rotation = .5*sum((np.cross(current_rotation[:, i], goal_rotation[:, i]) for i in range(3)))
        damping = 2*np.sqrt(OSC_KP)
        force = OSC_KP*(goal_position - data.site_xpos[tcp]) - damping*((jp - cp) @ data.qvel)
        torque = OSC_KP*error_rotation - damping*((jr - cr) @ data.qvel)
        lp = np.linalg.pinv(jpos @ inv @ jpos.T)
        lr = np.linalg.pinv(jori @ inv @ jori.T)
        lf = np.linalg.pinv(jac @ inv @ jac.T)
        nullspace = np.eye(7) - inv @ jac.T @ lf @ jac
        posture = (NULLSPACE_KP*(self.control.initial_joint - self.arm_positions('right'))
                   - 2*np.sqrt(NULLSPACE_KP)*data.qvel[dofs])
        return jac.T @ np.r_[lp @ force, lr @ torque] + data.qfrc_bias[dofs] + nullspace.T @ mass @ posture

    def _lift_force(self, full_mass):
        dof = self.joints.group_dofs('lift')[0]
        error = self.control.height - self.group('lift')[0]
        return (full_mass[dof, dof]*(LIFT_KP*error - 2*np.sqrt(LIFT_KP)*self.data.qvel[dof])
                + self.data.qfrc_bias[dof])

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


def robocasa_camera_calibration(sim):
    """Compare compiled bindings/local optics; record actual world extrinsics."""
    records = {}
    for name, (parent, position, quaternion, fovy) in ROBOCASA_CAMERAS.items():
        camera = sim.model.camera(sim.robot.camera_names[name]).id
        actual_parent = sim.model.body(int(sim.model.cam_bodyid[camera])).name
        expected_quat = np.asarray(quaternion) / np.linalg.norm(quaternion)
        # Geometry scaling moves the factory camera mounts with the robot.
        valid = (actual_parent == sim.robot.config.namespace + parent
                 and np.allclose(sim.model.cam_pos[camera], np.asarray(position) * SCALE, atol=1e-6, rtol=0)
                 and abs(np.dot(sim.model.cam_quat[camera], expected_quat)) > 1 - 1e-6
                 and abs(sim.model.cam_fovy[camera] - fovy) < 1e-6)
        records[name] = {'valid': bool(valid), 'parent': actual_parent,
                         'position_local': sim.model.cam_pos[camera].tolist(),
                         'quaternion_local_wxyz': sim.model.cam_quat[camera].tolist(),
                         'fovy_deg': float(sim.model.cam_fovy[camera]),
                         'position_world': sim.data.cam_xpos[camera].tolist(),
                         'rotation_world': sim.data.cam_xmat[camera].reshape(3, 3).tolist()}
    return {'valid': all(c['valid'] for c in records.values()), 'cameras': records,
            'reference': 'RoboCasa CAM_CONFIGS DEFAULT; Panda factory wrist fovy=75; no randomization'}
