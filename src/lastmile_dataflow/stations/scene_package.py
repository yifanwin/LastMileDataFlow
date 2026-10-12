"""Public strategy-independent scene package reader and station selection."""
from dataclasses import asdict
from pathlib import Path
import shutil

from ..io import digest, file_digest, read_json, write_json
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


def package_manifest(path, robot=None, geometry=None):
    """Verify sealed package; strategy/backend is deliberately not an argument."""
    root = Path(path).resolve()
    manifest = read_json(root / 'manifest.json')
    if manifest['schema_version'] != 'scene-station-package-v1' or manifest['status'] != 'sealed':
        raise ValueError('unsealed or unsupported scene package')
    checksums = read_json(root / 'checksums.json')
    for name, expected in checksums.items():
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file() or file_digest(target) != expected:
            raise ValueError('scene package checksum mismatch: ' + name)
    required = {'manifest.json', 'task.json', 'scene/model.mjb', 'scene/initial.npz', 'scene/version.json',
                'scene/instances.json', 'scene/checksums.json', 'stations/candidates.json',
                'stations/validated.json', 'stations/selected.json', 'stations/selection.json', 'validation/checks.json'}
    if not required <= set(checksums):
        raise ValueError('incomplete scene package')
    if digest(manifest['binding']) != manifest['robot_sensor_config_id'].removeprefix('rs-'):
        raise ValueError('robot/sensor identity mismatch')
    version = read_json(root / 'scene/version.json')
    if robot is not None and digest(robot_descriptor(robot)) != digest(manifest['binding']['robot']):
        raise ValueError('robot configuration mismatch; rebuild/revalidate required')
    if geometry is not None:
        for key, value in manifest['binding']['station_geometry'].items():
            if not hasattr(geometry, key) or getattr(geometry, key) != value:
                raise ValueError('sensor/station geometry mismatch: ' + key)
    task = read_json(root / 'task.json')
    rows = read_json(root / 'stations/validated.json')
    candidates = read_json(root / 'stations/candidates.json')
    keys = ('station_id', 'xy', 'grid_index')
    if [{k: r[k] for k in keys} for r in rows] != candidates:
        raise ValueError('not all original candidates were validated')
    selection = read_json(root / 'stations/selection.json')
    selected, expected = select_stations(rows, task['anchor_world'], manifest['selection_mode'], manifest['max_final_stations'])
    if selected != read_json(root / 'stations/selected.json') or selection != expected:
        raise ValueError('station selection mismatch')
    if not manifest['min_final_points'] <= len(selected) == manifest['station_count']:
        raise ValueError('station publication minimum/count mismatch')
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
    if not read_json(root / 'validation/checks.json')['valid']:
        raise ValueError('invalid scene checks')
    return {**manifest, 'package': str(root), 'package_checksums_sha256': file_digest(root / 'checksums.json')}


def bind_package(scene_path, manifest):
    """Copy verified immutable public input into a fresh execution run."""
    root = Path(manifest['package']); destination = Path(scene_path) / 'scene_dataset_input'
    if destination.exists():
        if read_json(destination / 'input_manifest.json') != manifest:
            raise ValueError('scene package resume identity mismatch')
        package_manifest(destination)
        for name, checksum in read_json(destination / 'scene/checksums.json').items():
            if file_digest(Path(scene_path) / 'scene' / name) != checksum:
                raise ValueError('execution scene checksum mismatch')
        return
    if (Path(scene_path) / 'scene').exists() or (Path(scene_path) / 'tasks').exists():
        raise ValueError('use a new execution run; cannot retrofit a scene package')
    shutil.copytree(root, destination)
    write_json(destination / 'input_manifest.json', manifest)
    shutil.copytree(root / 'scene', Path(scene_path) / 'scene')


def package_stations(scene_path, task):
    root = Path(scene_path) / 'scene_dataset_input'
    manifest = package_manifest(root)
    if task['task_id'] != manifest['task_id'] or task['target_body'] != read_json(root / 'task.json')['target_body']:
        raise ValueError('scene package task identity mismatch')
    return read_json(root / 'stations/selected.json'), {
        'schema_version': 'scene-dataset-execution-input-v1',
        'scene_dataset_id': manifest['scene_dataset_id'], 'sample_id': manifest['sample_id'],
        'robot_sensor_config_id': manifest['robot_sensor_config_id'],
        'lattice_regenerated': False, 'execution_point_count': manifest['station_count'],
        'station_ids': [s['station_id'] for s in read_json(root / 'stations/selected.json')],
        'package_checksums_sha256': file_digest(root / 'checksums.json')}
