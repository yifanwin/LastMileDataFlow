"""Figures of an existing base quality map run (`evaluate-stations --plot-from`).

Separate from computation: reads the saved arrays only, never solves IK or scores,
and needs no GPU. The computing workflow does not import this module.
"""
from pathlib import Path

import numpy as np

from ..data.visualization.quality_map import export_examples, export_quality_maps
from ..utils.io import read_json, write_json
from .station_map import MAP_KIND, dataset_scenes, heading_mean, recipe_scenes


def plot_station_map(run, output, *, render_examples=False):
    """Figures of an existing map run; reads saved arrays only, no IK, scoring or GPU.

    The position map is re-aggregated from the per-heading qualities with the
    current rule (mean over valid headings), so older runs plot consistently.
    `render_examples` reopens the scene for static renders and needs offscreen GL.
    """
    run, output = Path(run), Path(output)
    saved = read_json(run / 'config.json')
    if saved.get('kind') != MAP_KIND:
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
