"""Layered case verdicts and immutable attempt audit; unknown is not failure."""
import json
from pathlib import Path
import numpy as np
from ..io import read_json,file_digest,digest
from .pick import evaluate_pick


def case1_verdict(rows,source_base):
    success=[r for r in rows if r['execution']=='success' and r.get('status') not in ('infrastructure_error','budget_exhausted','interrupted')]
    origin=[r for r in rows if np.max(np.abs(np.array(r['base'])-source_base))<1e-6]
    if not success: return {'status':'unknown','reason':'no_physical_success_witness'}
    if any(r['execution']=='success' for r in origin): return {'status':'fail','reason':'initial_station_already_succeeds','feedback':'success_without_expected_difficulty'}
    difficult=[r for r in origin if r['execution']=='failure' or r['planning']=='no_solution']
    if not difficult: return {'status':'unknown','reason':'initial_station_not_tested'}
    return {'status':'pass','reason':'initial_configuration_difficult_other_station_physical_success',
            'scope':'tested arm/torso/yaw/grasp and frozen finite budgets only; not global impossibility or navigation',
            'success_witnesses':[r['attempt'] for r in success], 'difficulty_witnesses':[r['attempt'] for r in difficult]}


def audit_station_attempt(path):
    path=Path(path); issues=[]
    try:
        manifest=read_json(path/'artifacts.json')['files_sha256']
        actual={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}
        if set(manifest)!=actual: issues.append('manifest_file_set_mismatch')
        for name,sha in manifest.items():
            file=path/name
            if not file.resolve().is_relative_to(path.resolve()) or not file.is_file() or file_digest(file)!=sha: issues.append('modified_or_unsafe:'+name)
        cfg=read_json(path/'config.json'); meta=read_json(path/'attempt.json'); result=read_json(path/'result.json'); layers=read_json(path/'layers.json')
        if digest(cfg)!=meta['config_sha256']: issues.append('configuration_digest_mismatch')
        rows=[json.loads(v) for v in (path/'trajectory.jsonl').read_text().splitlines()]
        if len(rows)!=result['executed_steps'] or len(rows)!=meta['executed_steps'] or meta['status']!=result['status']: issues.append('step_count_or_status_mismatch')
        previous=None
        for i,r in enumerate(rows):
            before,after=r['state_before']['time_s'],r['state_after']['time_s']
            if r['step']!=i or len(r['raw_action'])!=20 or after<=before or previous is not None and abs(previous-before)>1e-9: issues.append('trajectory_discontinuity')
            if r['raw_action'][:3]!=[0.,0.,0.]: issues.append('nonzero_base_action')
            previous=after
        videos=read_json(path/'videos.json')['cameras']
        if not rows and (result['results']['task_completion']['status'] in ('success','failure') or layers['execution'] in ('success','failure')): issues.append('physical_label_without_execution')
        for p in read_json(path/'planning.json'):
            if p['status']!='success' and p['positions']: issues.append('failed_plan_contains_executable_waypoints')
        if not rows and (videos or (path/'replay.npz').exists() or list((path/'videos').glob('*.mp4'))): issues.append('video_without_execution')
        for name,info in videos.items():
            if info['frame_count']!=len(rows)+1 or len(info['frame_times_s'])!=len(rows)+1: issues.append('video_trajectory_frame_mismatch:'+name)
        if rows:
            samples=[json.loads(v) for v in (path/'physics.jsonl').read_text().splitlines()]
            initial=read_json(path/'pick_initial.json'); verdict=evaluate_pick(samples,initial)
            if result['results']['task_completion']['status']=='success' and verdict['status']!='success': issues.append('success_not_physically_supported')
            if layers['execution']=='success' and result['status']!='executed_success': issues.append('layer_success_mismatch')
            if abs(samples[-1]['time_s']-rows[-1]['state_after']['time_s'])>1e-9: issues.append('physics_trajectory_end_mismatch')
            with np.load(path/'replay.npz',allow_pickle=False) as z:
                if len(z['qpos'])!=len(rows)+1: issues.append('replay_count_mismatch')
                for i,r in enumerate(rows):
                    if not np.array_equal(z['qpos'][i+1],r['state_after']['qpos']): issues.append('replay_qpos_mismatch'); break
            source=read_json(path/'source.json'); frozen=Path(source['snapshot'])
            checksums=read_json(frozen/'checksums.json')
            if 'snapshot_checksums' in source and source['snapshot_checksums']!=checksums: issues.append('frozen_input_manifest_changed')
            for name,sha in checksums.items():
                file=frozen/name
                if not file.resolve().is_relative_to(frozen.resolve()) or not file.is_file() or file_digest(file)!=sha: issues.append('frozen_source_modified')
            version=read_json(frozen/'version.json')
            if digest({k:v for k,v in version.items() if k!='version_id'})!=version['version_id']: issues.append('source_version_digest_mismatch')
            if read_json(frozen/'version.json')['version_id']!=source['scene_version']: issues.append('source_version_mismatch')
        return {'valid':not issues,'issues':issues,'executed_steps':len(rows)}
    except Exception as exc: return {'valid':False,'issues':issues+[f'audit_error:{type(exc).__name__}:{exc}']}
