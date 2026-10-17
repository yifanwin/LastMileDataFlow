"""11D planning model, derived locally; original robot assets remain read-only."""
import copy
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np

V080_SHA = '4ea77366ca48ee453e7df139e39fa6532af49f3b'


def joint_names(side):
    if side not in ('left', 'right'):
        raise ValueError('invalid active arm')
    return tuple(['base_x', 'base_y', 'base_theta'] +
                 [f'{side}_arm_{i}' for i in range(7)] + ['torso_1'])


def derive_urdf(source, destination, bounds, torso_limits=(0., .738)):
    tree = ET.parse(source)
    root = tree.getroot()
    lo, hi = torso_limits
    for index, multiplier in ((1, 1), (2, -2), (3, 1)):
        limit = root.find(f"joint[@name='torso_{index}']/limit")
        a, b = float(limit.get('lower')), float(limit.get('upper'))
        allowed = sorted((a / multiplier, b / multiplier))
        lo, hi = max(lo, allowed[0]), min(hi, allowed[1])
    if lo >= hi:
        raise ValueError('empty coupled torso limits')
    velocities = []
    for index, multiplier in ((1, 1), (2, -2), (3, 1)):
        joint = root.find(f"joint[@name='torso_{index}']")
        limit = joint.find('limit')
        velocities.append(float(limit.get('velocity')) / abs(multiplier))
        if index != 1:
            old = joint.find('mimic')
            if old is not None:
                joint.remove(old)
            ET.SubElement(joint, 'mimic', joint='torso_1', multiplier=str(multiplier), offset='0')
    master = root.find("joint[@name='torso_1']/limit")
    master.set('lower', str(lo)); master.set('upper', str(hi))
    master.set('velocity', str(min(velocities)))
    for name, (lower, upper) in bounds.items():
        joint = root.find(f"joint[@name='{name}']")
        joint.set('type', 'revolute' if name == 'base_theta' else 'prismatic')
        joint.find('limit').set('lower', str(lower))
        joint.find('limit').set('upper', str(upper))
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return {'h_limits': [lo, hi], 'h_velocity_limit': min(velocities),
            'mimic': {'torso_2': ['torso_1', -2., 0.], 'torso_3': ['torso_1', 1., 0.]}}


def robot_config(template, sim, side, asset_root, urdf):
    k = copy.deepcopy(template['robot_cfg']['kinematics'])
    # V2 only targets the active TCP. Collision links are NOT target frames.
    allowed = ('base_link', 'collision_link_names', 'collision_spheres',
               'collision_sphere_buffer', 'self_collision_buffer', 'self_collision_ignore',
               'extra_links', 'extra_collision_spheres', 'cspace', 'lock_joints')
    k = {name: value for name, value in k.items() if name in allowed}
    k.update(tool_frames=[f'ee_{side}_tcp'], urdf_path=str(Path(urdf).resolve()),
             asset_root_path=str(Path(asset_root, 'urdf/meshes').resolve()),
             collision_spheres=str(Path(asset_root, 'rby1m_holobase_spheres.yml').resolve()),
             load_tool_frames_with_mesh=False)
    # Include the head branch for relative joint locking, NOT as a pose goal.
    k.setdefault('extra_links', {})['locked_head_branch'] = {
        'parent_link_name': 'link_head_2', 'link_name': 'locked_head_branch',
        'fixed_transform': [0, 0, 0, 1, 0, 0, 0], 'joint_type': 'FIXED',
        'joint_name': 'locked_head_branch_joint'}
    idle = 'right' if side == 'left' else 'left'
    locks = {}
    for group in ('head', idle + '_arm', 'left_gripper', 'right_gripper'):
        locks.update({n: float(q) for n, q in zip(sim.robot.groups[group], sim.robot.group(group))})
    locks.update(torso_0=0., torso_4=0., torso_5=0.)
    k['lock_joints'] = locks
    names = joint_names(side)
    old_names = k['cspace']['joint_names']
    for key, values in list(k['cspace'].items()):
        if isinstance(values, list) and len(values) == len(old_names):
            k['cspace'][key] = [values[old_names.index(n)] for n in names]
    k['cspace']['joint_names'] = list(names)
    # Penalize motion, not a stale absolute torso posture.
    for key in ('null_space_weight', 'cspace_distance_weight'):
        if key in k['cspace']:
            k['cspace'][key][-1] = 1.
    k['cspace'].pop('retract_config', None)
    k['cspace']['default_joint_position'] = [0., 0., 0.] + sim.robot.group(side + '_arm').tolist() + [float(sim.robot.group('torso')[1])]
    for key, h_limit in (('max_acceleration', 2.5), ('max_jerk', 250.)):
        value = k['cspace'].get(key, h_limit)
        values = list(value) if isinstance(value, list) else [float(value)] * len(names)
        values[-1] = min(values[-1], h_limit)
        k['cspace'][key] = values
    return {'kinematics': k}


def ordered_points(points, source_names, destination_names):
    if set(source_names) != set(destination_names) or len(source_names) != len(destination_names):
        raise ValueError('planner released forbidden or missing joints')
    return np.asarray(points)[..., [source_names.index(n) for n in destination_names]]


def retime_points(points, native_dt, control_hz, time_dilation):
    """Uniformly sample native positions onto the existing real control clock."""
    values = np.asarray(points, dtype=float)
    if not np.isfinite(native_dt) or native_dt <= 0 or not 0 < time_dilation <= 1:
        raise ValueError('invalid native trajectory timing')
    if len(values) < 2:
        return values
    duration = (len(values)-1)*native_dt/time_dilation
    original = np.arange(len(values))*native_dt/time_dilation
    requested = np.r_[np.arange(0., duration, 1./control_hz), duration]
    return np.stack([np.interp(requested, original, values[:, i]) for i in range(values.shape[1])], axis=1)


def static_ik_state(current):
    """Static IK is an endpoint query, not a one-control-tick motion."""
    result = current.clone()
    result.dt = None
    return result


def chain_state(state):
    """Do not reindex active-only optimizer knots with augmented joint names."""
    state.knot = None
    state.knot_dt = None
    return state


def minimum_mesh_query_dimension(max_radius, activation_distance=.01):
    if not np.isfinite(max_radius) or max_radius <= 0:
        raise ValueError('invalid collision radius')
    return 2*(max_radius + activation_distance + .01)


class PlanningModel:
    """Existing mobile torso model behind the common V2 description boundary."""
    movable_base = True
    fk_limits = (.001, .001)

    def __init__(self, sim, config, side, path, workspace):
        import yaml
        from ..scenes.geometry import body_pose
        from ..runtime.control.action_limits import local_yaw_bounds
        if config.operation_base_mode == 'fixed':
            raise ValueError('RBY-1 V2 uses holonomic_joint_planning')
        self.sim, self.side = sim, side
        self.fk_check_required = not sim.started
        self.names = joint_names(side)
        self.tcp_link = f'ee_{side}_tcp'
        self.attached_link = 'attached_object_' + side
        self.contact_links = [f'ee_finger_{side[0]}1', f'ee_finger_{side[0]}2']
        self.base = body_pose(sim, sim.model.body(sim.robot.config.namespace+'base').id)
        self.reference_yaw = float(sim.robot.group('base')[2])
        center, radius = workspace
        local = np.linalg.solve(self.base, np.r_[center[:2], self.base[2, 3], 1.])[:2]
        self.bounds = {'base_x': (local[0]-radius, local[0]+radius),
                       'base_y': (local[1]-radius, local[1]+radius),
                       'base_theta': local_yaw_bounds(sim.robot, self.reference_yaw)}
        root = Path(config.robot_planner_dir)
        urdf = Path(path) / 'mobile_torso_mimic.urdf'
        self.coupling = derive_urdf(root/'urdf/model_holobase.urdf', urdf, self.bounds, sim.robot.config.torso_limits)
        self.h_step_limit = self.coupling['h_velocity_limit'] * config.time_dilation * .9 / sim.robot.config.control_hz
        self.cfg = robot_config(yaml.safe_load((root/'rby1m_holobase.yml').read_text()), sim, side, root, urdf)
        self.cfg['kinematics']['collision_spheres'] = yaml.safe_load((root/'rby1m_holobase_spheres.yml').read_text())['collision_spheres']

    def arm_target(self, point):
        return np.asarray(point)[3:10]

    def height_target(self, point):
        return float(point[10])

    def current_joints(self):
        from ..scenes.geometry import body_pose
        relative = np.linalg.solve(self.base, body_pose(self.sim, self.sim.model.body(self.sim.robot.config.namespace+'base').id))
        return np.r_[relative[:2, 3], float(self.sim.robot.group('base')[2])-self.reference_yaw,
                     self.sim.robot.group(self.side+'_arm'), self.sim.robot.group('torso')[1]]

    def base_target_world(self, point):
        x, y, yaw = point[:3]
        local = np.eye(4)
        local[:2, 3] = [x, y]
        local[:2, :2] = [[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]]
        world = self.base @ local
        return np.r_[world[:2, 3], self.reference_yaw+yaw]
