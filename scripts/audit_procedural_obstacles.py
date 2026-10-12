"""Read-only revalidation of frozen real obstacle variants and their media."""
import argparse
from dataclasses import fields
from html.parser import HTMLParser
from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.construction.obstacle_config import ObstacleConfig
from lastmile_dataflow.construction.obstacle_sampling import substantial_asset
from lastmile_dataflow.stations.obstacle_selection import coverage_select
from lastmile_dataflow.recording.obstacle_views import visibility_pass
from lastmile_dataflow.io import digest, file_digest, read_json, write_json
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.preparation import SettleConfig
from lastmile_dataflow.validation.obstacle_augmentation import validate_augmented_scene


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attributes):
        if tag == 'img':
            self.paths.append(dict(attributes)['src'])


def audit(run):
    run = Path(run).resolve()
    summary = read_json(run / 'summary.json')
    frozen = read_json(run / 'frozen_config.json')
    config = ObstacleConfig(**frozen['augmentation'])
    policy = config.case_policy
    robot = RobotConfig(**frozen['robot'])
    provenance = read_json(run / 'provenance.json')
    package = Path(__file__).resolve().parents[1] / 'src/lastmile_dataflow'
    code_current = all((package / path).is_file() and file_digest(package / path) == value
                       for path, value in provenance['source_files'].items())
    rows = []
    rgb = []
    for item in summary['variants']:
        ident = item['variant_id']
        path = run / 'variants' / ident
        if item['status'] != 'accepted':
            rows.append({'variant_id': ident, 'valid': False, 'reason': item['status']})
            continue
        edits = read_json(path / 'edits.json')
        checks = read_json(path / 'checks.json')
        before = Simulation.from_snapshot(path / 'before_scene', robot, target=config.target_instance)
        after = Simulation.from_snapshot(path / 'scene', robot, target=config.target_instance)
        try:
            recomputed = validate_augmented_scene(after, edits, checks['settling'], before, config)
            added = [entry for entry in after.catalog if entry['instance_id'].startswith('procedural_obstacle_')]
            categories = [entry['category'] for entry in added]
            count_valid = len(added) == item['requested_count'] and set(config.mandatory_categories) <= set(categories)
            if policy and policy.case == 'Case1':
                count_valid &= (categories.count('Chair') == 1 and 1 <= len(added) <= 3
                                and all(p['kind'] == ('ground' if p['category'] == 'Chair' else 'surface') for p in edits))
            if policy and policy.case == 'Case2':
                support_bodies = {o['body'] for o in read_json(run / 'baseline/task.json')['support_obstacles']}
                count_valid &= (6 <= len(added) <= 12 and all(p['kind'] == 'surface' and p['support_instance'] in support_bodies for p in edits)
                                and sum(substantial_asset({'category': p['category'], 'size': p['measured_size_m']}, policy) for p in edits) >= policy.min_substantial)
            site_groups = {after.model.site(i).name: int(after.model.site_group[i]) for i in range(after.model.nsite)
                           if after.model.site(i).name.startswith('procedural_obstacle_')}
            source_geometry_equal = True
            # Per-body geom identities survive append-only additions. Check all source geom
            # shapes and local transforms rather than asserting only the source XML hash.
            for g in range(before.model.ngeom):
                b0 = int(before.model.geom_bodyid[g])
                body_name = before.model.body(b0).name
                b1 = after.model.body(body_name).id
                h = int(after.model.body_geomadr[b1]) + g - int(before.model.body_geomadr[b0])
                source_geometry_equal &= bool(before.model.geom_type[g] == after.model.geom_type[h]
                    and np.allclose(before.model.geom_size[g], after.model.geom_size[h], atol=1e-8, rtol=0)
                    and np.allclose(before.model.geom_pos[g], after.model.geom_pos[h], atol=1e-8, rtol=0)
                    and np.allclose(before.model.geom_quat[g], after.model.geom_quat[h], atol=1e-8, rtol=0))
            pair = read_json(path / 'rgb/pair.json')
            image_rows = []
            for record in pair['images']:
                image_path = Path(record['path'])
                pixels = imageio.imread(image_path)
                valid = bool(pixels.shape == (config.views.get('height', 480), config.views.get('width', 640), 3)
                             and np.std(pixels) > 1 and record['pair_id'] == pair['pair_id'])
                row = {'path': str(image_path.relative_to(run)), 'sha256': file_digest(image_path),
                       'shape': list(pixels.shape), 'valid': valid}
                rgb.append(row)
                image_rows.append(row)
            different = []
            for view in ('top', 'oblique_a', 'oblique_b'):
                a = imageio.imread(path / f'rgb/before_{view}.png')
                b = imageio.imread(path / f'rgb/after_{view}.png')
                different.append(bool(np.any(a != b)))
            visibility = pair.get('visible_pixels_by_instance', {})
            visible = bool(visibility) and all(visibility['before/' + view][config.target_instance] >= frozen['baseline']['min_target_pixels']
                          and all(visibility['after/' + view][name] >= frozen['baseline']['min_target_pixels']
                                  for name in [config.target_instance] + [e['instance_id'] for e in added])
                          for view in ('top', 'oblique_a', 'oblique_b'))
            if pair.get('visibility_policy', '').startswith('three_view_union'):
                visible = visibility_pass(visibility, [e['instance_id'] for e in added], config.target_instance,
                                          frozen['baseline']['min_target_pixels'], True)
            station_valid = True
            station_audit = {}
            if policy:
                original = read_json(run / 'baseline/stations.json')
                comparison = read_json(path / 'navigation/comparison.json')
                stations = comparison['stations_after']
                final = read_json(path / 'stations_final.json')
                expected, selection = coverage_select(stations, read_json(run / 'baseline/task.json')['anchor_world'], policy.max_final_stations)
                station_valid = (len(stations) == len(original) and
                    [s['station_id'] for s in stations] == [s['station_id'] for s in original] and
                    all(np.array_equal(s['xy'], o['xy']) for s, o in zip(stations, original)) and
                    all(s.get('independent_simulation') for s in stations) and
                    final == expected and item['valid_stations_after'] == sum(s['geometry'] == 'valid' for s in stations) and
                    len(final) == item['final_station_count'] <= policy.max_final_stations)
                station_audit = {'all_original_candidates_checked': len(stations), 'actual_passed': selection['input_count'],
                                 'final': len(final), 'coverage_recomputed': final == expected, 'valid': bool(station_valid)}
            rows.append({'variant_id': ident, 'valid': bool(recomputed['valid'] and count_valid
                          and source_geometry_equal and all(r['valid'] for r in image_rows) and all(different) and visible
                          and station_valid and all(g == 5 for g in site_groups.values())),
                         'physical_recheck': recomputed['valid'], 'added_count': len(added),
                         'mandatory_categories_present': count_valid, 'source_collision_geometry_unchanged': source_geometry_equal,
                         'receptacle_site_groups': site_groups, 'views_changed': different,
                         'visibility_requirement_passed': visible, 'visibility_policy': pair.get('visibility_policy', 'all_objects_each_view'),
                         'stations': station_audit,
                         'max_source_delta_vs_control_m': max(r['delta_vs_control_m'] for r in recomputed['source_effects']['source_objects'])})
        finally:
            before.close()
            after.close()
    parser = Images()
    parser.feed((run / 'index.html').read_text())
    linked = all((run / name).is_file() for name in parser.paths)
    sources_unchanged = all(file_digest(r['source_xml']) == r['source_sha256']
                            for r in read_json(run / 'assets/retrieval.json')['records'] if r['status'] == 'loadable')
    registered = all(r.get('thor_registered', False) for r in read_json(run / 'assets/retrieval.json')['records'] if r['status'] == 'loadable') if policy else True
    lineage_valid = True
    if 'physical_parent_run' in provenance:
        parent = Path(provenance['physical_parent_run'])
        original = read_json(parent / 'provenance.json')
        changed = {key for key in provenance['source_files'] if provenance['source_files'][key] != original['source_files'].get(key)}
        lineage_valid = changed == {'recording/obstacle_views.py'}
        for ident in [v['variant_id'] for v in summary['variants']]:
            for name in ('scene', 'before_scene'):
                for filename in ('model.mjb', 'initial.npz', 'version.json', 'instances.json', 'checksums.json'):
                    lineage_valid &= file_digest(run / 'variants' / ident / name / filename) == file_digest(parent / 'variants' / ident / name / filename)
            lineage_valid &= file_digest(run / 'variants' / ident / 'navigation/comparison.json') == file_digest(parent / 'variants' / ident / 'navigation/comparison.json')
    result = {'valid': all(r['valid'] for r in rows) and linked and sources_unchanged and code_current and registered and lineage_valid,
              'scope': 'same_context_read_only_frozen_scene_and_media_revalidation_not_independent_agent_review',
              'current_code_matches_run_manifest': code_current, 'source_asset_xmls_unchanged': sources_unchanged,
              'registered_assets_only_verified': registered,
              'physical_parent_lineage_verified': bool(lineage_valid),
              'browser_image_links_resolve': linked, 'browser_image_count': len(parser.paths),
              'raw_rgb_count': len(rgb), 'variants': rows, 'rgb': rgb}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    write_json(args.output, result)
    print(result['valid'], result['raw_rgb_count'], args.output)
    raise SystemExit(0 if result['valid'] else 1)
