"""最小场景记录读取。"""
from pathlib import Path
from ..utils.io import rows


def load_scenes(dataset, samples=None):
    root = Path(dataset).resolve()
    records = [r for r in rows(root / 'scenes.jsonl') if r['status'] == 'accepted']
    if samples and set(samples) - {r['scene_id'] for r in records}:
        raise ValueError('requested scene not found')
    return [{**r, 'scene_path': str(root / r['scene'])} for r in records
            if not samples or r['scene_id'] in samples]
