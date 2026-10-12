"""All-outcome index, independent of success-only training eligibility views."""
import os
from pathlib import Path
import tempfile

from ..io import canonical, file_digest, read_json


def rebuild_canonical_index(run_path):
    root = Path(run_path).resolve()
    candidates = sorted((root / 'scenes').glob('*/attempts/*/*/*/attempt.json'))
    if not candidates:
        candidates = sorted((root / 'attempts').glob('*/attempt.json'))
    entries = []
    seen = set()
    for path in candidates:
        attempt = path.parent
        identity = attempt.resolve()
        if identity in seen:
            continue  # Status symlinks are views, not independent episodes.
        seen.add(identity)
        value = read_json(path)
        manifest = attempt / 'canonical/manifest.json'
        result = attempt / 'canonical/outcomes.json'
        canonical_value = read_json(manifest) if manifest.is_file() else None
        outcome = read_json(result) if result.is_file() else None
        entries.append({'episode_id': value['attempt_id'], 'attempt': str(attempt.relative_to(root)),
                        'termination_status': value['status'],
                        'executed_steps': canonical_value.get('executed_steps') if canonical_value else value.get('executed_steps', 0),
                        'legacy_executed_steps': value.get('executed_steps', 0),
                        'canonical_manifest': str(manifest.relative_to(root)) if canonical_value else None,
                        'canonical_status': 'sealed' if canonical_value and canonical_value['sealed'] else 'incomplete' if canonical_value else 'legacy_partial',
                        'manifest_sha256': file_digest(manifest) if canonical_value else None,
                        'recording_complete': canonical_value.get('recording_complete') if canonical_value else False,
                        'success': outcome.get('success') if outcome else None,
                        'task_id': canonical_value.get('task_id') if canonical_value else value.get('task_id'),
                        'scene_id': canonical_value.get('scene_id') if canonical_value else None})
    fd, temporary = tempfile.mkstemp(prefix='.canonical-index-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            for entry in entries:
                stream.write(canonical(entry) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / 'canonical_index.jsonl')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(entries)
