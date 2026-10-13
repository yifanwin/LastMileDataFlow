"""Explicit legacy scene audit; not imported by normal dataset workflows."""
from pathlib import Path
import json
import numpy as np
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.io import digest, file_digest, read_json
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.construction.obstacle_config import ObstacleConfig
from lastmile_dataflow.runtime.preparation import SettleConfig
def audit_scene_dataset(root, physical=False, audit_inputs=None):
    """Checksum/identity/selection audit, optionally reload physical frozen scenes."""
    root = Path(root).resolve(); manifest = read_json(root / 'manifest.json'); errors = []; records = []
    portable_inputs = None
    if audit_inputs is not None:
        audit_inputs = Path(audit_inputs).resolve()
        portable_inputs = read_json(audit_inputs / 'manifest.json')
        if (portable_inputs.get('schema_version') != 'scene-audit-inputs-v1'
                or portable_inputs.get('scene_dataset_id') != manifest['scene_dataset_id']
                or portable_inputs.get('dataset_manifest_sha256') != file_digest(root / 'manifest.json')):
            raise ValueError('portable audit inputs dataset identity mismatch')
    expected = read_json(root / 'checksums.json')
    for name, checksum in expected.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_digest(path) != checksum:
            errors.append('dataset checksum mismatch: ' + name)
    samples = [json.loads(line) for line in (root / 'samples.jsonl').read_text().splitlines()]
    for sample in samples:
        try:
            destination = root / sample['path']
            if read_json(destination / 'manifest.json') != sample:
                raise ValueError('sample index mismatch')
            for name, checksum in read_json(destination / 'checksums.json').items():
                target = (destination / name).resolve()
                if not target.is_relative_to(destination) or file_digest(target) != checksum:
                    raise ValueError('sample checksum mismatch: ' + name)
            packages = list((destination / 'robot_sensor_configs').iterdir())
            for package in packages:
                pm = read_json(package / 'manifest.json')
                if pm['sample_id'] != sample['sample_id']:
                    raise ValueError('package sample identity mismatch')
                if physical:
                    robot = RobotConfig(**read_json(package / 'scene/version.json')['robot'])
                    task = read_json(package / 'task.json')
                    sim = Simulation.from_snapshot(package / 'scene', robot, target=task['target_body'])
                    try:
                        if not np.isfinite(sim.state_vector()).all():
                            raise ValueError('nonfinite physical snapshot')
                        if sample['case'] != 'Case3':
                            from lastmile_dataflow.validation.obstacle_augmentation import validate_augmented_scene
                            p = read_json(destination / 'construction/provenance.json')
                            if portable_inputs is None:
                                config_values = read_json(Path(p['construction_run']) / 'frozen_config.json')['augmentation']
                            else:
                                record = portable_inputs['configs'][sample['sample_id']]
                                location = (audit_inputs / record['file']).resolve()
                                if not location.is_relative_to(audit_inputs) or file_digest(location) != record['sha256']:
                                    raise ValueError('portable augmentation config checksum/path mismatch')
                                config_values = read_json(location)
                            config = ObstacleConfig(**config_values)
                            control = Simulation.from_snapshot(destination / 'construction/control_scene', robot, target=task['target_body'])
                            try:
                                stored = read_json(package / 'validation/checks.json')
                                placements = read_json(destination / 'construction/edits.json')
                                capacity = None
                                if (destination / 'construction/capacity.json').exists():
                                    from lastmile_dataflow.catalog.edit_assets import EditAssetCatalog
                                    from lastmile_dataflow.construction.obstacle_sampling import asset_geometry, surface_points
                                    from lastmile_dataflow.construction.surface_capacity import valid_certificate
                                    from lastmile_dataflow.runtime.obstacle_edit_session import ObstacleEditSession
                                    from .obstacle_augmentation import _capacity_probe
                                    capacity = read_json(destination / 'construction/capacity.json')
                                    if not valid_certificate(sim, placements, capacity):
                                        raise ValueError('capacity certificate state/domain mismatch')
                                    run = Path(p['construction_run'])
                                    baseline = Simulation.from_snapshot(run / 'baseline/scene', robot, target=task['target_body'])
                                    try:
                                        assets = EditAssetCatalog(run / 'assets/catalog.json')
                                        geometry = asset_geometry(assets)
                                        footprint_filter = read_json(run / 'assets/footprint_filter.json')
                                        excluded = set(footprint_filter['excluded']) if footprint_filter.get('applied', True) else set()
                                        geometry = {k:g for k,g in geometry.items() if k not in excluded}
                                        points = surface_points(support_regions(baseline, task), config.case_policy.surface_spacing_m)
                                        if config.case_policy.case == 'Case1':
                                            points = [q for q in points if np.linalg.norm(np.asarray(q['xy'])-task['anchor_world'][:2]) <= config.case_policy.near_target_m]
                                        with ObstacleEditSession(baseline, assets, SettleConfig(**config.settle)) as probe:
                                            replay, _, _ = _capacity_probe(probe, sim, placements, geometry, points, config, None)
                                        if not valid_certificate(sim, placements, replay) or replay['domain_sha256'] != capacity['domain_sha256']:
                                            raise ValueError('capacity exhaustive replay failed')
                                    finally:
                                        baseline.close()
                                checks = validate_augmented_scene(sim, placements, stored['settling'], control, config, capacity_evidence=capacity)
                                if not checks['valid']:
                                    raise ValueError('physical collision/support/case/source-effect audit failed')
                            finally:
                                control.close()
                        else:
                            j = sim.model.joint(task['joint_name']).id
                            if abs(float(sim.data.qpos[sim.model.jnt_qposadr[j]]) - task['joint_initial']) > 1e-12:
                                raise ValueError('Case3 articulation changed')
                    finally:
                        sim.close()
            if sample['case'] != 'Case3':
                import imageio.v2 as imageio
                from lastmile_dataflow.recording.obstacle_views import visibility_pass
                packet = read_json(destination / 'media/pair.json')
                edits = read_json(destination / 'construction/edits.json')
                names = [f'procedural_obstacle_{i}' for i in range(len(edits))]
                threshold = read_json(packages[0] / 'manifest.json')['binding']['station_geometry']['min_target_pixels']
                if not visibility_pass(packet['visible_pixels_by_instance'], names, sample['instance_id'], threshold, True):
                    raise ValueError('paired media segmentation coverage failed')
                for view in ('top', 'oblique_a', 'oblique_b'):
                    before = imageio.imread(destination / f'media/before_{view}.png')
                    after = imageio.imread(destination / f'media/after_{view}.png')
                    if before.shape != after.shape or not np.any(before != after) or float(before.std()) <= 1 or float(after.std()) <= 1:
                        raise ValueError('paired RGB empty/unchanged')
                if not all(r['pair_id'] == packet['pair_id'] and (destination / 'media' / r['path']).is_file() for r in packet['images']):
                    raise ValueError('paired image identity/path mismatch')
            records.append({'sample_id': sample['sample_id'], 'valid': True})
        except Exception as exc:
            records.append({'sample_id': sample['sample_id'], 'valid': False, 'reason': str(exc)})
    if len(samples) != manifest['sample_count'] or len({s['sample_id'] for s in samples}) != len(samples):
        errors.append('sample count/identity mismatch')
    return {'schema_version': 'scene-dataset-audit-v1', 'valid': not errors and all(r['valid'] for r in records),
        'dataset': str(root), 'scope': 'physical_frozen_scene_and_checksums' if physical else 'checksums_identity_station_selection',
        'errors': errors, 'samples': records, 'sample_count': len(samples), 'task_execution': 'not_performed'}


if __name__ == '__main__':
    import argparse
    from lastmile_dataflow.io import write_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--physical', action='store_true')
    parser.add_argument('--audit-inputs', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit_scene_dataset(args.dataset, args.physical, args.audit_inputs)
    write_json(args.output, report)
    raise SystemExit(0 if report['valid'] else 1)
