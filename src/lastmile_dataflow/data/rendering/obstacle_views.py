"""Bounded paired observer search with REAL segmentation occlusion checks."""
from pathlib import Path
import math
import shutil

from .edit_views import render_edit_pair, RenderError
from ...io import write_json


def visibility_pass(visibility, obstacle_names, target, min_pixels, coverage_union):
    views = ('top', 'oblique_a', 'oblique_b')
    if coverage_union:
        # Union alone can accept two wall-only obliques if top sees everything.
        # Every pair must show the original target and at least one actual addition.
        return (all(visibility['before/' + v][target] >= min_pixels and
                    any(visibility['after/' + v][n] >= min_pixels for n in obstacle_names) for v in views)
                and all(any(visibility['after/' + v][n] >= min_pixels for v in views)
                        for n in set(obstacle_names) | {target}))
    return all(visibility['before/' + v][target] >= min_pixels and
               all(visibility['after/' + v][n] >= min_pixels for n in set(obstacle_names) | {target}) for v in views)


def render_obstacle_pair(before, after, before_graph, after_graph, obstacle_names, target,
                         context, path, *, sample_id, config, min_pixels=24, coverage_union=False):
    path = Path(path)
    angles = ((math.pi / 4, -3 * math.pi / 4), (math.pi / 4, -math.pi / 4),
              (math.pi / 4, 0.), (math.pi / 4, math.pi / 2),
              (0., math.pi / 4), (math.pi / 2, math.pi / 4))
    open_side_pairs = ((0., -math.pi / 4), (-math.pi / 4, -math.pi / 2))
    # A wider clutter framing can put every +45° observer outside a wall.
    # Include pairs where NEITHER oblique is +45°; keep the search bounded.
    angles = open_side_pairs + angles if coverage_union else angles + open_side_pairs
    trials = []
    for i, pair in enumerate(angles):
        folder = path.parent / 'camera_trials' / f'C{i:02d}'
        try:
            packet = render_edit_pair(before, after, before_graph, after_graph,
                     set(obstacle_names) | {target}, folder, sample_id=sample_id,
                     config=config, context_nodes=context, oblique_azimuths_rad=pair,
                     visibility_instances=sorted(set(obstacle_names) | {target}))
        except RenderError as exc:
            trials.append({'trial': i, 'status': 'render_error', 'reason': str(exc)})
            continue
        visibility = packet['visible_pixels_by_instance']
        valid = visibility_pass(visibility, obstacle_names, target, min_pixels, coverage_union)
        trials.append({'trial': i, 'azimuths_rad': pair, 'status': 'visible' if valid else 'occluded',
                       'visible_pixels_by_instance': visibility})
        if not valid:
            continue
        shutil.copytree(folder, path)
        packet['selected_camera_trial'] = i
        packet['camera_selection'] = 'bounded_programmatic_segmentation_checks_no_wall_hiding_no_scene_edits'
        packet['visibility_policy'] = 'three_view_union_with_each_pair_observed' if coverage_union else 'all_objects_each_view'
        for item in packet['images']:
            item['path'] = Path(item['path']).name
        write_json(path / 'pair.json', packet)
        write_json(path / 'camera_search.json', {'min_visible_pixels': min_pixels, 'trials': trials})
        return packet
    write_json(path.parent / 'camera_search_failed.json', {'min_visible_pixels': min_pixels, 'trials': trials})
    raise RenderError('no_paired_observer_views_with_target_and_all_obstacles_visible')
