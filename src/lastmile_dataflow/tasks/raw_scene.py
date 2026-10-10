"""Task discovery from the actual exported model, not historical success labels."""
from pathlib import Path
import re
import mujoco
import numpy as np

from ..io import digest
from ..planning.curobo import body_pose
from ..scenes.geometry import body_points, descendants


LABELS = {'Cup': '杯子', 'Mug': '马克杯', 'Bottle': '瓶子', 'Bowl': '碗',
          'Plate': '盘子', 'Book': '书', 'Apple': '苹果', 'CellPhone': '手机',
          'Fridge': '冰箱', 'Microwave': '微波炉', 'Dresser': '抽屉柜',
          'Desk': '书桌', 'SideTable': '边桌', 'Safe': '保险柜', 'Doorway': '房门'}
OPEN_TYPES = {'Doorway', 'Door', 'Fridge', 'Microwave', 'Dresser', 'Desk',
              'SideTable', 'Cabinet', 'Drawer', 'Safe', 'WashingMachine', 'ClothesDryer'}


def local_bounds(sim, body):
    points = body_points(sim, body)
    transform = body_pose(sim, body)
    local = (points - transform[:3, 3]) @ transform[:3, :3]
    return local.min(axis=0), local.max(axis=0)


def robot_footprint(sim):
    """Actual base and wheel geometry projected into the base frame."""
    from ..scenes.geometry import geom_points
    base = body_pose(sim, sim.model.body('robot_0/base').id)
    points = []
    for g in range(sim.model.ngeom):
        name = sim.model.body(int(sim.model.geom_bodyid[g])).name
        if name == 'robot_0/base' or name.startswith('robot_0/') and 'wheel' in name.lower():
            points.append((geom_points(sim, g) - base[:3, 3]) @ base[:3, :3])
    if not points:
        raise ValueError('missing chassis geometry')
    points = np.concatenate(points)
    return {'radius_m': float(np.linalg.norm(points[:, :2], axis=1).max()),
            'min': points.min(axis=0).tolist(), 'max': points.max(axis=0).tolist(),
            'method': 'base_origin_enclosing_circle_of_base_and_wheel_geoms'}


def discover_tasks(sim, metadata, config):
    tasks, skipped = [], []
    for instance in sim.catalog:
        name, asset, category = instance['instance_id'], instance.get('asset_id'), instance.get('category')
        if not asset or name.startswith('robot_0/'):
            continue
        b = instance['body_id']
        try:
            lo, hi = local_bounds(sim, b)
        except ValueError:
            skipped.append({'instance': name, 'reason': 'missing_collision_geometry'})
            continue
        pose = body_pose(sim, b)
        center = pose[:3, :3] @ ((lo + hi) / 2) + pose[:3, 3]
        label = LABELS.get(category, category or asset)
        source_id = instance.get('source_object_id') or name
        # Unique instance qualifier is necessary for repeated same-category objects.
        qualifier = f'房间{instance.get("room_id")}中编号{source_id}'
        common = {'instance_id': name, 'asset_id': asset, 'category': category,
                  'room_id': instance.get('room_id'), 'dimensions_m': (hi-lo).tolist(),
                  'source_object_id': source_id, 'center_world': center.tolist()}
        free = any(sim.model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE
                   for j in range(int(sim.model.body_jntadr[b]),
                                  int(sim.model.body_jntadr[b] + sim.model.body_jntnum[b])))
        if 'pick' in config.operations and min(hi-lo) <= config.gripper_width_m + 1e-9 and free:
            task = {**common, 'operation': 'pick', 'target_body': name,
                    'instruction': f'拿起{qualifier}的{label}。', 'anchor_world': center.tolist(),
                    'local_bounds': [lo.tolist(), hi.tolist()]}
            task['task_id'] = 'pick-' + digest([name, asset])[:12]
            tasks.append(task)
        elif 'pick' in config.operations and min(hi-lo) <= config.gripper_width_m:
            skipped.append({'instance': name, 'operation': 'pick', 'reason': 'not_free_moving_instance'})
        if 'open' not in config.operations or category not in OPEN_TYPES:
            continue
        obj = metadata.get('objects', {}).get(name, {})
        joints = obj.get('name_map', {}).get('joints', {})
        found = 0
        for joint_name, semantic in sorted(joints.items()):
            try:
                j = sim.model.joint(joint_name).id
            except KeyError:
                continue
            kind = sim.model.jnt_type[j]
            if kind not in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE):
                continue
            semantic = (str(semantic) + ' ' + joint_name).lower()
            if 'handle' in semantic or not sim.model.jnt_limited[j]:
                continue
            moving = int(sim.model.jnt_bodyid[j]); sub = descendants(sim.model, moving)
            handles = [body for body in sorted(sub) if 'handle' in sim.model.body(body).name.lower()
                       or 'handle' in str(obj.get('name_map', {}).get('bodies', {}).get(
                           sim.model.body(body).name, '')).lower()]
            handle = handles[0] if handles else moving
            try:
                hlo, hhi = local_bounds(sim, handle)
            except ValueError:
                continue
            hp = body_pose(sim, handle)
            contact = hp[:3, :3] @ ((hlo+hhi)/2) + hp[:3, 3]
            q0 = float(sim.data.qpos[sim.model.jnt_qposadr[j]])
            low, high = map(float, sim.model.jnt_range[j])
            # Closed end nearest to zero; do not change the source articulation pose.
            closed, opened = (low, high) if abs(low) <= abs(high) else (high, low)
            travel = abs(opened-closed)
            if travel < 1e-4 or abs(q0-closed)/travel >= config.open_fraction:
                skipped.append({'instance': name, 'joint': joint_name, 'reason': 'already_open_or_no_travel'})
                continue
            unit = 'slide' if kind == mujoco.mjtJoint.mjJNT_SLIDE else 'hinge'
            threshold = min(travel, max(config.open_fraction * travel,
                            config.open_slide_m if unit == 'slide' else config.open_hinge_rad))
            target_q = closed + np.sign(opened-closed) * threshold
            if abs(target_q-q0) < 1e-4:
                continue
            found += 1
            task = {**common, 'operation': 'open', 'joint_name': joint_name,
                    'joint_kind': unit, 'joint_initial': q0, 'joint_goal': float(target_q),
                    'joint_range': [low, high], 'joint_axis_world': sim.data.xaxis[j].tolist(),
                    'joint_anchor_world': sim.data.xanchor[j].tolist(),
                    'target_body': sim.model.body(moving).name,
                    'handle_body': sim.model.body(handle).name, 'explicit_handle': bool(handles),
                    'handle_local_bounds': [hlo.tolist(), hhi.tolist()],
                    'anchor_world': contact.tolist(),
                    'instruction': f'打开{qualifier}的{label}的第{found}个' + ('抽屉。' if unit == 'slide' else '门。')}
            task['task_id'] = 'open-' + digest([name, joint_name])[:12]
            tasks.append(task)
        if not found:
            skipped.append({'instance': name, 'operation': 'open', 'reason': 'no_eligible_articulation'})
    return tasks, skipped


def grasp_candidates(task, assets_dir, max_rows=1024):
    """Read asset grasp poses, or generate bbox pinch poses without modifying assets."""
    asset = task['asset_id']
    if Path(asset).name != asset or asset in ('.', '..'):
        raise ValueError('unsafe asset identity')
    empty_sources=[]
    if task['operation'] == 'pick':
        for directory in ('droid', 'droid_objaverse'):
            path = Path(assets_dir)/'grasps'/directory/asset/(asset+'_grasps_filtered.npz')
            if path.is_file():
                with np.load(path, allow_pickle=False) as z:
                    transforms = z['transforms'].astype(float)
                if transforms.size == 0:
                    empty_sources.append(str(path)); continue
                if transforms.ndim != 3 or transforms.shape[1:] != (4, 4):
                    raise ValueError('invalid grasp shape')
                ids = np.linspace(0, len(transforms)-1, min(max_rows, len(transforms)), dtype=int)
                result = []
                for index in ids:
                    p = transforms[index].copy()
                    if not np.isfinite(p).all() or not np.allclose(p[3], [0,0,0,1], atol=1e-5):
                        continue
                    u, s, v = np.linalg.svd(p[:3, :3])
                    if np.max(np.abs(s-1)) > .02 or np.linalg.det(p[:3, :3]) <= 0:
                        continue
                    p[:3, :3] = u@v
                    result.append({'pose_local': p, 'body': task['target_body'],
                                   'source': str(path), 'row': int(index)})
                if result:
                    return result
    lo, hi = np.asarray(task.get('handle_local_bounds', task.get('local_bounds')))
    center = (lo+hi)/2; width_axis = int(np.argmin(hi-lo))
    result = []
    for axis in range(3):
        if axis == width_axis:
            continue
        for sign in (-1, 1):
            y = np.eye(3)[width_axis]; z = sign*np.eye(3)[axis]; x = np.cross(y,z)
            for flip in (1, -1):
                pose = np.eye(4); pose[:3, :3] = np.column_stack((flip*x,flip*y,z))
                pose[:3, 3] = center
                result.append({'pose_local': pose,
                               'body': task.get('handle_body', task['target_body']),
                               'source': 'procedural_bbox_pinch_v1', 'row': len(result),
                               'empty_asset_grasp_sources':empty_sources})
    return result
