"""Revalidate ALL original dense points against a prepared native-profile scene.

Reuses the profile transplant snapshot, not old trial caches or cuRobo validity.
No physics stepping, obstacle resampling, or changes to the source snapshot.
"""
from dataclasses import asdict
from pathlib import Path
import shutil
import numpy as np

from ..config import RobotConfig, construct
from ..construction.obstacle_config import ObstacleConfig
from ..io import read_json, write_json, file_digest
from ..runtime.simulation import Simulation
from ..stations.no_edit_sampling import disk_samples
from ..stations.obstacle_selection import coverage_select
from ..validation.obstacle_augmentation import collision_pairs, load_support, case_construction_check
from .obstacle_augmentation import evaluate_stations


def revalidate_native_stations(variant, native_scene, config, output):
    variant = Path(variant).resolve(); native_scene = Path(native_scene).resolve()
    parent = variant.parent.parent; output = Path(output).resolve()
    if config.operation_backend != 'molmobot' or config.augmented_variant:
        raise ValueError('use a MolmoBot baseline config without augmented_variant')
    frozen = read_json(parent/'frozen_config.json')
    for key in ('radius_m', 'spacing_m'):
        if getattr(config,key) != frozen['baseline'][key]:
            raise ValueError('original dense geometry config mismatch: '+key)
    if not read_json(variant/'checks.json')['valid']:
        raise ValueError('source augmented layout was not accepted')
    task = read_json(parent/'baseline/task.json')
    candidates = read_json(parent/'baseline/stations.json')
    # Geometry never changes identity when changing control profile or image size.
    expected = disk_samples(task['anchor_world'],config.radius_m,config.spacing_m)
    stripped = [{k:s[k] for k in ('station_id','xy','grid_index')} for s in candidates]
    if stripped != expected:
        raise ValueError('original full dense candidate set mismatch')
    version = read_json(native_scene/'version.json')
    robot = construct(RobotConfig, version['robot'])
    if robot.control_profile != 'molmobot-native-v1' or robot.control_hz != 10 or robot.physics_dt != .004:
        raise ValueError('native MolmoBot robot profile required')
    source_version = read_json(variant/'scene/version.json')
    if version['source'] != source_version['source']:
        raise ValueError('native scene source mismatch')
    provenance = read_json(native_scene/'rebuild_provenance.json')
    if Path(provenance['variant']).resolve() != variant:
        raise ValueError('native transplant references a different source variant')
    expected_placements = [{k:p[k] for k in ('asset_id','pose')} for p in read_json(variant/'edits.json')]
    if provenance['placements'] != expected_placements:
        raise ValueError('native transplant obstacle placements mismatch')
    output.mkdir(parents=True, exist_ok=False)
    target = output/'variants'/variant.name
    target.mkdir(parents=True)
    write_json(output/'summary.json',{'status':'running','scope':'native_profile_station_revalidation'})
    write_json(output/'baseline/task.json',task)
    write_json(output/'baseline/stations.json',candidates)
    write_json(output/'frozen_config.json',{'augmentation':frozen['augmentation'],
               'baseline':asdict(config),'robot':asdict(robot)})
    sim = Simulation.from_snapshot(native_scene,robot,target=task['target_body'])
    state = sim.state_vector().copy()
    try:
        aug = construct(ObstacleConfig,frozen['augmentation'])
        edits = read_json(variant/'edits.json')
        finite = bool(np.isfinite(state).all())
        collision = collision_pairs(sim,edits,aug,settled=True)
        support = load_support(sim,edits,aug)
        case = case_construction_check(sim,edits,aug)
        checks = {'valid':bool(finite and collision['valid'] and support['valid'] and case['valid']),
                  'finite':finite,'collision':collision,'support':support,'case_construction':case,
                  'scope':'native_snapshot_geometry_support_case_not_new_settle_acceptance',
                  'source_effects':'not_recomputed_no_new_physics_steps','task_completion':'unknown'}
        write_json(target/'checks.json',checks)
        if not checks['valid']:
            raise ValueError('native scene collision/support/case check failed')
        rows = evaluate_stations(sim,task,config,frozen_stations=candidates)
        final,selection = coverage_select(rows,task['anchor_world'],20)
        if not np.array_equal(state,sim.state_vector()):
            raise RuntimeError('independent station validation changed source scene state')
        write_json(target/'navigation/comparison.json',{'stations_after':rows,
                   'note':'all original dense points; fresh Simulation/MjData per point; native robot and 1024x576 visibility'})
        write_json(target/'stations_final.json',final)
        write_json(target/'station_selection.json',selection)
        if not final:
            raise ValueError('no native-profile station passed actual initialization and visibility')
        shutil.copytree(native_scene,target/'scene')
        shutil.copy2(variant/'edits.json',target/'edits.json')
        lineage={'schema_version':'native-station-revalidation-v1','source_variant':str(variant),
                 'native_scene':str(native_scene),'dense_candidate_count':len(candidates),
                 'actual_valid_count':selection['input_count'],'final_count':len(final),
                 'source_scene_state_unchanged':True,'no_new_physics_steps':True,
                 'robot':asdict(robot),'validation_config':asdict(config),
                 'candidate_sha256':file_digest(parent/'baseline/stations.json'),
                 'parent_checks_sha256':file_digest(variant/'checks.json'),
                 'edits_sha256':file_digest(variant/'edits.json'),
                 'native_scene_files_sha256':{p.name:file_digest(p) for p in sorted(native_scene.iterdir()) if p.is_file()}}
        write_json(target/'native_revalidation.json',lineage)
        summary={'status':'completed','accepted_count':1,'case':aug.case_policy.case,
                 'dense_candidate_count':len(candidates),'actual_valid_count':selection['input_count'],
                 'final_station_count':len(final),'execution_scope':'native_profile_full_dense_station_revalidation',
                 'task_completion':'unknown','variants':[{'variant_id':variant.name,'status':'accepted'}]}
        write_json(output/'summary.json',summary)
        return target
    except BaseException as exc:
        write_json(output/'summary.json',{'status':'failed','reason':str(exc),'task_completion':'unknown'})
        raise
    finally:
        sim.close()
