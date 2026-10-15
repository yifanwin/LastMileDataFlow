"""最小场景记录读取与覆盖选点。"""
from pathlib import Path
from ..io import rows
from .augmentation.selection import coverage_select


def select_stations(stations, anchor, method='coverage', limit=20):
    if method == 'coverage':
        return coverage_select(stations, anchor, limit)
    if method != 'all_valid':
        raise ValueError('unknown station selection')
    selected = [s for s in stations if s['geometry'] == 'valid']
    return selected, {'method': method, 'selected_ids': [s['station_id'] for s in selected]}


def load_scenes(dataset, samples=None):
    root = Path(dataset).resolve()
    records = rows(root / 'scenes.jsonl')
    if samples and set(samples) - {r['scene_id'] for r in records}:
        raise ValueError('requested scene not found')
    return [{**r, 'scene_path': str(root / r['scene'])} for r in records
            if r['status'] == 'accepted' and (not samples or r['scene_id'] in samples)]
