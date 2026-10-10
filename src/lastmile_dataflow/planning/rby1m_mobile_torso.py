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
