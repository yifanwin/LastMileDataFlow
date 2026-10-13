"""Research episode completion and adapter input checks; no tree hashing/replay."""
from pathlib import Path

import numpy as np

from ..io import digest, read_json
from ..recording.protocol import SCHEMA, rows, safe_path


def episode_record(path):
    root = Path(path)
    record = read_json(root / 'episode.json')
    if record.get('schema_version') != 'research-episode-v1' or record.get('status') != 'complete':
        raise ValueError('episode has not been committed under the research protocol')
    if digest(read_json(root / 'config.json')) != record['config_sha256']:
        raise ValueError('episode configuration identity mismatch')
    for name in record['required_files']:
        if not safe_path(root, name).is_file():
            raise ValueError('missing episode data: ' + name)
    if (root / 'canonical/manifest.json').is_file():
        manifest = read_json(root / 'canonical/manifest.json')
        if manifest.get('status') != 'complete' or manifest['episode_id'] != record['episode_id']:
            raise ValueError('episode/trajectory completion mismatch')
        for row in rows(root / 'raw/state-index.jsonl'):
            if not safe_path(root, row['path']).is_file():
                raise ValueError('missing recorded state')
        for row in rows(root / 'raw/observations.jsonl'):
            if row['status'] == 'recorded' and not safe_path(root, row['path']).is_file():
                raise ValueError('missing recorded observation')
            if row.get('depth', {}).get('status') == 'recorded' and not safe_path(root, row['depth']['path']).is_file():
                raise ValueError('missing recorded depth')
    return record


def export_inputs(path):
    """Check only the state/action/image references consumed by this adapter."""
    root = Path(path)
    record = episode_record(root)
    manifest = read_json(root / 'canonical/manifest.json')
    if manifest['schema_version'] != SCHEMA or manifest.get('status') != 'complete':
        raise ValueError('unsupported or unfinished trajectory')
    if record['episode_id'] != manifest['episode_id'] or not record['recording_complete'] or not manifest['recording_complete']:
        raise ValueError('incomplete episode recording')
    if not record.get('data_valid') or not manifest.get('data_valid'):
        raise ValueError('episode data semantics failed')
    data = {key: rows(root / name) for key, name in {
        'frames': 'canonical/frames.jsonl', 'states': 'canonical/robot-state.jsonl',
        'actions': 'canonical/executed-actions.jsonl', 'commands': 'raw/commands.jsonl',
        'observations': 'raw/observations.jsonl'}.items()}
    frames, states, actions = data['frames'], data['states'], data['actions']
    if len(frames) != len(actions) + 1 or len(states) != len(frames) or len(frames) != manifest['frame_count'] or len(actions) != manifest['executed_steps']:
        raise ValueError('state/action/frame count mismatch')
    for i, (frame, state) in enumerate(zip(frames, states)):
        if frame['frame_id'] != i or state['frame_id'] != i or frame['timestamp'] != state['timestamp']:
            raise ValueError('frame/state alignment mismatch')
        if not np.isfinite(frame['timestamp']) or (i and frame['timestamp'] <= frames[i-1]['timestamp']):
            raise ValueError('noncontinuous frame time')
        if any(not np.isfinite(np.asarray(group['position'], float)).all() for group in state['groups'].values()):
            raise ValueError('nonfinite robot state')
    for i, action in enumerate(actions):
        if action['step_id'] != i or action['frame_before_id'] != i or action['frame_after_id'] != i+1 or action['t_start'] != frames[i]['timestamp'] or action['t_end'] != frames[i+1]['timestamp']:
            raise ValueError('action/frame alignment mismatch')
        command = data['commands'][action['command_submitted_id']]
        vector = np.asarray(command['submitted_action'], float)
        if command['kind'] != 'command_submitted' or command['controller_profile'] != manifest['controller_profile'] or vector.shape != (20,) or not np.isfinite(vector).all():
            raise ValueError('invalid executed command')
    return record, manifest, data
