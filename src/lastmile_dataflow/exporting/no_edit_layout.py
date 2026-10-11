"""Readable no-edit output names and immutable, self-contained rollout bundles."""
from pathlib import Path
import os
import re
import shutil
import uuid

from ..io import read_json, write_json


def component(value):
    name = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(value)).strip('-_').lower()
    if not name:
        raise ValueError('empty output name')
    return name


def assign_output_names(tasks):
    """Keep protocol task IDs; use scene-local deterministic semantic folder names."""
    counts = {}
    for task in tasks:
        instance = str(task.get('instance_id', task['task_id'])).split('/')[-1]
        category = component(task.get('category') or instance.split('_')[0])
        number = re.search(r'(\d+)$', instance)
        base = category + (number.group(1) if number else '')
        counts[base] = counts.get(base, 0) + 1
        task['output_name'] = base if counts[base] == 1 else f'{base}-{task["operation"]}{counts[base]}'


def attempt_group(task, station):
    operation, station_id = task['operation'], station['station_id']
    if operation not in ('pick', 'open') or not re.fullmatch(r'[A-Za-z0-9_-]+', station_id):
        raise ValueError('unsafe no-edit attempt group')
    return (operation, station_id)


def index_attempt(path, status):
    """Status links avoid relocating planner paths or changing sealed evidence."""
    path = Path(path)
    bucket = {'success': 'success', 'failure': 'fail'}.get(status, status)
    if bucket not in ('success', 'fail', 'planning_no_solution', 'infrastructure_error', 'incomplete', 'simulation_failure'):
        bucket = 'unknown'
    link = path.parent / bucket / path.name
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(Path('..') / path.name, target_is_directory=True)
    return str(link)


def export_rollout(run_path, scene_name, task, segment, *, eligible):
    """Copy real evidence once; publish atomically, never rewrite an existing bundle."""
    source = Path(segment['attempt'])
    destination = Path(run_path) / 'datasets' / scene_name / task['operation'] / task.get('output_name', task['task_id']) / source.name
    if (destination / 'sample.json').is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / ('.export-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        shutil.copytree(source, temporary / 'rollout')
        failures = []
        for evidence in segment.get('s0_failure_evidence', []):
            failed_source = Path(evidence['attempt'])
            relative = Path('s0_failures') / failed_source.name
            shutil.copytree(failed_source, temporary / relative)
            failures.append({**evidence, 'attempt':str(relative),
                'videos':str(relative / 'videos.json'),
                'third_person_video':str(relative / 'videos/third_person_camera.mp4')
                    if (temporary / relative / 'videos/third_person_camera.mp4').is_file() else None})
        media = read_json(temporary / 'rollout/videos.json')
        write_json(temporary / 'sample.json', {
            'schema_version':'no-edit-dataset-v2', 'instruction':task['instruction'],
            'task':task, 'source_attempt':str(source), 'task_eligible_at_export':eligible,
            'trajectory':'rollout/trajectory.jsonl', 'observations':'rollout/observations.json',
            'canonical_manifest':'rollout/canonical/manifest.json' if (temporary / 'rollout/canonical/manifest.json').is_file() else None,
            'canonical_checksums':'rollout/canonical/checksums.json' if (temporary / 'rollout/canonical/checksums.json').is_file() else None,
            'videos':{camera:str(Path('rollout') / entry['path']) for camera, entry in media['cameras'].items()},
            'video_metadata':'rollout/videos.json', 's0':segment['start'], 's1':segment['goal'],
            'path':segment['path'], 's0_failure_evidence':failures})
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination
