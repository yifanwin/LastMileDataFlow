"""Figures of an existing base quality map run (`evaluate-stations --plot-from`).

Separate from computation: reads the saved arrays only, never solves IK or scores,
and needs no GPU. The computing workflow does not import this module.
"""
from pathlib import Path
import json

import numpy as np

from ..data.visualization.quality_map import export_examples, export_quality_maps
from ..utils.io import read_json, write_json
from .station_map import QUALITY_MAP_KINDS, dataset_scenes, heading_mean, recipe_scenes


def plot_station_map(run, output, *, render_examples=False):
    """Figures of an existing map run; reads saved arrays only, no IK, scoring or GPU.

    The position map is re-aggregated from the per-heading qualities with the
    current rule (mean over valid headings), so older runs plot consistently.
    `render_examples` reopens the scene for static renders and needs offscreen GL.
    """
    run, output = Path(run), Path(output)
    saved = read_json(run / 'config.json')
    if saved.get('kind') not in QUALITY_MAP_KINDS:
        raise ValueError(f'{run} is not a base quality map run')
    output.mkdir(parents=True, exist_ok=False)
    scenes = None
    result = {'source_run': str(run.resolve()), 'position_aggregation': 'mean over valid headings', 'scenes': {}}
    for scene_id in saved['scenes']:
        source = run / scene_id
        if not (source / 'map.npz').is_file():
            result['scenes'][scene_id] = 'no map.npz'
            continue
        with np.load(source / 'map.npz') as z:
            data = {key: z[key] for key in z.files}
        data['paths'] = _load_paths(source)
        data['position_quality'], data['valid_headings'] = heading_mean(data['status'], data['quality'])
        figures = export_quality_maps(data, read_json(source / 'scene.json'), read_json(source / 'summary.json'),
                                      output / scene_id)
        if render_examples:
            if scenes is None:
                scenes = {s.scene_id: s for s in (recipe_scenes(saved['recipe']) if saved.get('recipe') else
                          dataset_scenes(saved['dataset'], saved.get('assets_dir'), saved.get('samples')))}
            export_examples(scenes[scene_id], data, figures)
        result['scenes'][scene_id] = sorted(p.name for p in figures.iterdir())
    write_json(output / 'plot.json', result)
    return output


def _load_paths(source):
    paths = {}
    for file in sorted(source.glob('candidates-*.npz')):
        with np.load(file) as z:
            if 'summary_json' not in z.files:
                continue
            for text in z['summary_json']:
                query = json.loads(str(text))
                retained = query['retained_ids']
                entries = []
                for candidate in query['candidates']:
                    if candidate['candidate_id'] in retained:
                        quality = candidate['quality']
                        entries.append(dict(id=candidate['candidate_id'], full=quality['full'], post=quality['post'],
                            phase=quality['full_bottleneck']['phase'],
                            tcp=[np.asarray(sample['tcp'])[:3, 3].tolist() for sample in quality['samples']]))
                if entries:
                    paths[str(query['query'])] = entries
    return paths
