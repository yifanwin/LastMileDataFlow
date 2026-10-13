"""Research scene identity and station selection; no package seals."""
from dataclasses import asdict
from pathlib import Path
import shutil

from ..io import digest, file_digest, read_json, write_json
from ..runtime.snapshot_identity import check_snapshot_identity
from .obstacle_selection import coverage_select


def select_stations(rows, anchor, method='coverage', limit=20):
    if method == 'coverage':
        return coverage_select(rows, anchor, limit)
    if method != 'all_valid':
        raise ValueError('unknown station selection')
    selected = [s for s in rows if s['geometry'] == 'valid']
    if len({s['station_id'] for s in rows}) != len(rows):
        raise ValueError('duplicate station identity')
    return selected, {'method': 'all_valid', 'input_count': len(selected), 'final_count': len(selected),
                      'selected_ids': [s['station_id'] for s in selected], 'discarded': []}


def robot_descriptor(robot):
    value = asdict(robot)
    value.pop('planning_adapter')  # not a physical/sensor identity
    model = value.pop('model_path')
    value['robot_model_sha256'] = file_digest(model)
    return value


def package_manifest(path, robot=None, geometry=None, *, scene_root=None):
    """Check scientific identities without rereading physics or a file-tree seal."""
    root = Path(path).resolve()
    manifest = read_json(root / 'manifest.json')
    if manifest['schema_version'] != 'scene-station-package-research-v2' or manifest['status'] != 'complete':
        raise ValueError('unsupported or incomplete research scene package; use a new dataset')
    scene_root = Path(scene_root) if scene_root is not None else root / 'scene'
    check_snapshot_identity(scene_root, manifest['scene_identity'])
    if digest(manifest['binding']) != manifest['robot_sensor_config_id'].removeprefix('rs-'):
        raise ValueError('robot/sensor identity mismatch')
    version = read_json(scene_root / 'version.json')
    if robot is not None and digest(robot_descriptor(robot)) != digest(manifest['binding']['robot']):
        raise ValueError('robot configuration mismatch; rebuild/revalidate required')
    if geometry is not None:
        for key, value in manifest['binding']['station_geometry'].items():
            if not hasattr(geometry, key) or getattr(geometry, key) != value:
                raise ValueError('sensor/station geometry mismatch: ' + key)
    task = read_json(root / 'task.json')
    stations = read_json(root / 'stations.json')
    rows, candidates = stations['validated'], stations['candidates']
    keys = ('station_id', 'xy', 'grid_index')
    if [{k: r[k] for k in keys} for r in rows] != candidates:
        raise ValueError('not all original candidates were validated')
    selection = stations['selection']
    selected, expected = select_stations(rows, task['anchor_world'], manifest['selection_mode'], manifest['max_final_stations'])
    if [r['station_id'] for r in selected] != stations['selected_ids'] or selection != expected:
        raise ValueError('station selection mismatch')
    if digest(selected) != manifest['stations_sha256']:
        raise ValueError('station identity mismatch')
    if not manifest['min_final_points'] <= len(selected) == manifest['station_count'] <= 20:
        raise ValueError('station minimum/count mismatch')
    for row in selected:
        if row.get('independent_simulation') is not True or row.get('non_robot_qpos_unchanged') is not True or row.get('new_solver_warnings'):
            raise ValueError('unverified station')
        if row.get('visibility', {}).get('target') != task['target_body'] or row['visibility']['visible_pixels'] < manifest['binding']['station_geometry']['min_target_pixels']:
            raise ValueError('station visibility mismatch')
        import numpy as np
        if not row.get('initial') or not np.isfinite(row['xy']).all() or not np.allclose(row['initial']['base'][:2], row['xy'], atol=1e-12, rtol=0):
            raise ValueError('station initialization pose mismatch')
    if task['task_id'] != manifest['task_id'] or version['source']['scene_id'] != manifest['scene_id']:
        raise ValueError('scene/task identity mismatch')
    if manifest.get('construction_valid') is not True:
        raise ValueError('invalid scene checks')
    return {**manifest, 'package': str(root), 'package_identity': digest(manifest)}


def bind_package(scene_path, manifest):
    """Copy verified immutable public input into a fresh execution run."""
    root = Path(manifest['package']); destination = Path(scene_path) / 'scene_dataset_input'
    if destination.exists():
        if read_json(destination / 'input_manifest.json') != manifest:
            raise ValueError('scene package resume identity mismatch')
        package_manifest(destination, scene_root=Path(scene_path) / 'scene')
        check_snapshot_identity(Path(scene_path) / 'scene', manifest['scene_identity'])
        return
    if (Path(scene_path) / 'scene').exists() or (Path(scene_path) / 'tasks').exists():
        raise ValueError('use a new execution run; cannot retrofit a scene package')
    shutil.copytree(root, destination, ignore=shutil.ignore_patterns('scene'))
    write_json(destination / 'input_manifest.json', manifest)
    shutil.copytree(root / 'scene', Path(scene_path) / 'scene')


def package_stations(scene_path, task):
    root = Path(scene_path) / 'scene_dataset_input'
    manifest = package_manifest(root, scene_root=Path(scene_path) / 'scene')
    if task['task_id'] != manifest['task_id'] or task['target_body'] != read_json(root / 'task.json')['target_body']:
        raise ValueError('scene package task identity mismatch')
    stations = read_json(root / 'stations.json')
    lookup = {s['station_id']: s for s in stations['validated']}
    selected = [lookup[sid] for sid in stations['selected_ids']]
    return selected, {
        'schema_version': 'scene-dataset-execution-input-v1',
        'scene_dataset_id': manifest['scene_dataset_id'], 'sample_id': manifest['sample_id'],
        'robot_sensor_config_id': manifest['robot_sensor_config_id'],
        'lattice_regenerated': False, 'execution_point_count': manifest['station_count'],
        'station_ids': stations['selected_ids'],
        'package_identity': manifest['package_identity']}
