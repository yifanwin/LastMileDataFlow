"""Bind verified coverage stations to their exact frozen augmented scene.

No lattice regeneration, prefix truncation, or fallback to raw scenes is allowed.
"""
from dataclasses import asdict
from pathlib import Path
import shutil
import numpy as np

from ..io import read_json, write_json, file_digest, digest
from .obstacle_selection import coverage_select


def variant_manifest(variant, robot, config):
    variant = Path(variant).resolve()
    run = variant.parent.parent
    task = read_json(run / 'baseline/task.json')
    frozen = read_json(run / 'frozen_config.json')
    version = read_json(variant / 'scene/version.json')
    summary = read_json(run / 'summary.json')
    if summary['status'] != 'completed' or not read_json(variant / 'checks.json')['valid']:
        raise ValueError('augmented input is not an accepted completed variant')
    stored_robot = dict(version['robot']); supplied_robot = asdict(robot)
    for value in (stored_robot, supplied_robot):
        value['model_path'] = str(Path(value['model_path']).resolve())
    if digest(stored_robot) != digest(supplied_robot):
        raise ValueError('augmented stations robot profile mismatch; rebuild and revalidate first')
    for key in ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels'):
        if getattr(config, key) != frozen['baseline'][key]:
            raise ValueError('augmented station validation config mismatch: ' + key)
    checksums = read_json(variant / 'scene/checksums.json')
    if set(checksums) != {'model.mjb', 'initial.npz', 'version.json', 'instances.json'}:
        raise ValueError('incomplete augmented scene checksums')
    for name, checksum in checksums.items():
        if file_digest(variant / 'scene' / name) != checksum:
            raise ValueError('augmented scene checksum mismatch: ' + name)
    stations = read_json(variant / 'stations_final.json')
    selection = read_json(variant / 'station_selection.json')
    original = read_json(variant / 'navigation/comparison.json')['stations_after']
    expected, recomputed = coverage_select(original, task['anchor_world'], 20)
    if stations != expected or selection != recomputed or not 1 <= len(stations) <= 20:
        raise ValueError('augmented final stations do not match verified coverage selection')
    for s in stations:
        if (s.get('geometry') != 'valid' or s.get('independent_simulation') is not True
                or not s.get('initial') or s.get('new_solver_warnings')
                or s.get('non_robot_qpos_unchanged') is not True
                or s.get('visibility', {}).get('target') != task['target_body']):
            raise ValueError('unverified augmented station: ' + s['station_id'])
        if not np.isfinite(s['xy']).all() or not np.allclose(s['initial']['base'][:2], s['xy'], atol=1e-12, rtol=0):
            raise ValueError('augmented station pose mismatch')
    names = ['stations_final.json', 'station_selection.json', 'checks.json', 'navigation/comparison.json']
    if (variant / 'native_revalidation.json').exists():
        native = read_json(variant / 'native_revalidation.json')
        if native['robot'] != version['robot'] or native['dense_candidate_count'] != len(original):
            raise ValueError('native revalidation provenance mismatch')
        if native['actual_valid_count'] != recomputed['input_count'] or native['final_count'] != len(stations):
            raise ValueError('native revalidation station counts mismatch')
        if native['validation_config'] != frozen['baseline']:
            raise ValueError('native station validation configuration mismatch')
        for name, checksum in native['native_scene_files_sha256'].items():
            if file_digest(variant/'scene'/name) != checksum:
                raise ValueError('native revalidation snapshot lineage mismatch: '+name)
        if not native['source_scene_state_unchanged']:
            raise ValueError('native revalidation changed source state')
        names.append('native_revalidation.json')
    hashes = {name: file_digest(variant / name) for name in names}
    hashes.update({'scene/' + name: file_digest(variant / 'scene' / name)
                   for name in (*checksums, 'checksums.json')})
    if (variant/'native_revalidation.json').exists():
        hashes.update({'scene/'+name: checksum for name, checksum in native['native_scene_files_sha256'].items()})
    hashes['baseline/task.json'] = file_digest(run / 'baseline/task.json')
    hashes['frozen_config.json'] = file_digest(run / 'frozen_config.json')
    return {'schema_version': 'augmented-station-input-v1', 'variant': str(variant),
            'case': frozen['augmentation']['construction']['case'],
            'scene_id': version['source']['scene_id'], 'task_id': task['task_id'],
            'target_body': task['target_body'], 'asset_id': task['asset_id'],
            'station_count': len(stations), 'station_ids': [s['station_id'] for s in stations],
            'selection_method': selection['method'], 'files_sha256': hashes}


def bind_scene_inputs(scene_path, manifest):
    """Copy only into a fresh run; resume must have identical immutable inputs."""
    scene_path = Path(scene_path)
    source = Path(manifest['variant']); run = source.parent.parent
    destination = scene_path / 'augmented_input'
    if destination.exists():
        if read_json(destination / 'manifest.json') != manifest:
            raise ValueError('augmented input resume manifest mismatch')
        for name, checksum in manifest['files_sha256'].items():
            path = scene_path / name if name.startswith('scene/') else destination / name
            if file_digest(path) != checksum:
                raise ValueError('augmented input resume checksum mismatch: ' + name)
        return
    if (scene_path / 'scene').exists() or (scene_path / 'tasks').exists():
        raise ValueError('cannot retrofit augmented stations into an existing raw run; use new run-id')
    destination.mkdir(parents=True)
    for name in manifest['files_sha256']:
        if name.startswith('scene/'):
            src = source / name; dst = scene_path / name
        else:
            src = (run if name in ('baseline/task.json', 'frozen_config.json') else source) / name
            dst = destination / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    write_json(destination / 'manifest.json', manifest)


def execution_stations(scene_path, task):
    """Return exact selected rows; cached/trial rows outside these IDs must fail."""
    root = Path(scene_path) / 'augmented_input'
    manifest = read_json(root / 'manifest.json')
    if task['task_id'] != manifest['task_id'] or task['target_body'] != manifest['target_body']:
        raise ValueError('augmented task identity mismatch')
    p = root / 'stations_final.json'
    if file_digest(p) != manifest['files_sha256']['stations_final.json']:
        raise ValueError('augmented execution stations checksum mismatch')
    stations = read_json(p)
    if [s['station_id'] for s in stations] != manifest['station_ids'] or len(stations) > 20:
        raise ValueError('augmented execution station IDs mismatch')
    return stations, {**manifest, 'execution_point_count': len(stations),
                      'lattice_regenerated': False, 'selection_digest': digest(stations)}
