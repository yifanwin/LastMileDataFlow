"""Read-only posthoc evidence, never overwrite a sealed attempt or publication."""
import gzip
from pathlib import Path
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed

import mujoco

from ..io import digest, file_digest, read_json, write_json
from ..recording.protocol import code_identity
from ..validation.canonical import audit_canonical
from ..validation.no_edit import evaluate_mobile_pick
from ..runtime.no_edit_evidence import EXECUTION_SEMANTICS
from .lastmile_datasets import write_rows


_MODELS = {}

def _review_trial(trial, run):
    models = _MODELS
    start = time.monotonic()
    attempt = Path(trial['attempt']).resolve()
    if run not in attempt.parents:
        raise ValueError('foreign trial attempt')
    config = read_json(attempt/'config.json')
    outcome = read_json(attempt/'outcome.json')
    refs = {name:file_digest(attempt/name) for name in
            ('config.json','initial_state.json','outcome.json','physics.jsonl.gz','artifacts.json')}
    verdict = {'status':'unknown','reason':'no_pick_physics_trace'}
    audit = {'valid':False,'issues':['not_checked']}
    reconstruction = None
    if config['task']['operation'] == 'pick':
        with gzip.open(attempt/'physics.jsonl.gz','rt') as stream:
            # Exclude contacts/monitor plumbing; keep all actual force-aware facts.
            import json
            samples = [r for line in stream if (r := json.loads(line)).get('sampler') == 'force-aware-v3']
        if samples:
            init_path = attempt/'task_evidence_initial.json'
            if init_path.exists():
                initial = read_json(init_path)['initial']
                reconstruction = {'source':'recorded_monitor_initial'}
            else:
                # Frozen MJB supplies exact finger body IDs, NOT guessed observed counts.
                model_path = attempt.parents[3]/'scene/model.mjb'
                if str(model_path) not in models:
                    models[str(model_path)] = mujoco.MjModel.from_binary_path(str(model_path))
                model = models[str(model_path)]
                state = read_json(attempt/'initial_state.json')
                finger_ids = {side:[model.body('robot_0/ee_finger_'+side[0]+str(i)).id for i in (1,2)]
                              for side in ('left','right')}
                seen = {int(k) for sample in samples for k in sample['finger_forces_n']}
                closed = [side for side in finger_ids if list(attempt.glob('v080-closed-*-'+side))]
                matched = [side for side, ids in finger_ids.items() if seen and seen <= set(ids)]
                sides = closed if len(closed) == 1 else matched
                if len(sides) != 1:
                    initial = None
                    verdict = {'status':'unknown','reason':'ambiguous_legacy_monitor_arm'}
                else:
                    side = sides[0]; idle = 'right' if side == 'left' else 'left'
                    initial = {'base':state['robot']['base'],'head':state['robot']['head'],
                               'idle_arm':state['robot'][idle+'_arm'],
                               'height_m':state['target']['pose_world_xyz_wxyz'][2],
                               'target':state['target']['mjcf_body'],'fingers':finger_ids[side],
                               'workspace_center_xy':config['task']['anchor_world'][:2],
                               'workspace_radius_m':config['protocol']['radius_m']}
                    reconstruction = {'source':'legacy_initial_state_and_frozen_model',
                                      'arm':side,'arm_evidence':'closed_planner_directory' if len(closed)==1 else 'recorded_contact_ids',
                                      'compiled_model_sha256':file_digest(model_path),
                                      'no_retries':not bool(outcome.get('retry_history'))}
                    if outcome.get('retry_history') or config.get('path'):
                        # Initial state is not the monitor baseline after navigation/retry.
                        initial = None
                        verdict = {'status':'unknown','reason':'legacy_monitor_baseline_not_reconstructable'}
            if initial is not None:
                verdict = evaluate_mobile_pick(samples, initial)
        audit = audit_canonical(attempt) if verdict['status']=='success' else {
            'valid':False, 'scope':'not_required_for_non_success_task_verdict', 'issues':[]}
    stored = read_json(attempt/'artifacts.json')['files_sha256']
    if any(stored.get(name) != sha for name,sha in refs.items() if name != 'artifacts.json'):
        raise ValueError('physical evidence differs from original attempt seal')
    after = {name:file_digest(attempt/name) for name in refs}
    if refs != after:
        raise ValueError('source changed during review')
    eligible = verdict['status']=='success' and audit['valid']
    record = {'attempt':str(attempt),'station_id':trial['station_id'],'trial':trial['trial'],
              'recorded_status':trial['status'],'recorded_reason':outcome['reason'],
              'execution_termination':{'status':'timeout','reason':outcome['reason']},
              'task_outcome':verdict,'canonical_audit':audit,'monitor_initial_reconstruction':reconstruction,
              'verified_posthoc_success':eligible,'source_files_sha256':refs,'source_unchanged':True,
              'review_wall_s':time.monotonic()-start,'not_continuous_recovery_training':True}
    return record

def review(run, output, *, workers=4):
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('review CPU workers must be 1..4')
    run, output = Path(run).resolve(), Path(output).resolve()
    if output == run or run in output.parents:
        raise ValueError('derived review must be outside source run')
    if output.exists():
        raise FileExistsError('unique new review output required')
    tables = sorted(run.glob('scenes/*/tasks/*/trials.json'))
    if not tables:
        raise ValueError('no source trial tables')
    before = {str(p.relative_to(run)): file_digest(p) for p in tables}
    output.mkdir(parents=True)
    write_json(output/'run.json', {'schema_version':'lastmile-timeout-review-v1',
               'source_run':str(run),'source_trial_tables_sha256':before,
               'execution_semantics_version':EXECUTION_SEMANTICS,'code':code_identity(),
               'posthoc_only':True,'source_unchanged_required':True})
    records, rates = [], []
    for table in tables:
        trials = read_json(table)
        replacements = {}
        pending = [trial for trial in trials if trial['status']=='timeout']
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {pool.submit(_review_trial, trial, run):trial for trial in pending}
            for future in as_completed(futures):
                trial = futures[future]
                record = future.result()
                replacements[(trial['station_id'],trial['trial'])] = record['verified_posthoc_success']
                records.append(record)
                records.sort(key=lambda r:(r['station_id'],r['trial']))
                write_rows(output/'reviews.jsonl',records)
                write_json(output/'progress.json',{'status':'running','reviewed':len(records)})
                print(Path(record['attempt']).name, record['task_outcome']['status'],
                      'audit='+str(record['canonical_audit']['valid']), flush=True)
        for sid in sorted({r['station_id'] for r in trials}):
            rows = [r for r in trials if r['station_id']==sid]
            denominator = sum(r['status'] in ('success','failure','planning_no_solution','timeout') for r in rows)
            old = sum(r['status']=='success' for r in rows)
            recovered = sum(replacements.get((r['station_id'],r['trial']),False) for r in rows)
            rates.append({'station_id':sid,'source_trial_table':str(table),'trials':denominator,
                          'recorded_successes':old,'additional_audited_posthoc_successes':recovered,
                          'recorded_rate':old/denominator if denominator else None,
                          'posthoc_evidence_rate':(old+recovered)/denominator if denominator else None,
                          'posthoc_not_formal_recalibration':True})
    after_tables = {str(p.relative_to(run)):file_digest(p) for p in tables}
    if before != after_tables:
        raise ValueError('source trial table changed')
    write_rows(output/'station_posthoc_rates.jsonl',rates)
    summary = {'status':'completed','reviewed_timeouts':len(records),
               'verified_posthoc_successes':sum(r['verified_posthoc_success'] for r in records),
               'task_verdict_counts':{status:sum(r['task_outcome']['status']==status for r in records)
                                      for status in ('success','failure','infrastructure_error','unknown','simulation_failure')},
               'audit_valid':sum(r['canonical_audit']['valid'] for r in records),
               'source_unchanged':True,'source_tables_sha256':after_tables,
               'not_relabelled':True,'not_formal_recalibration':True,'train_episodes_added':0}
    write_json(output/'summary.json',summary)
    write_json(output/'progress.json',summary)
    write_json(output/'manifest.json',{'schema_version':'lastmile-timeout-review-v1','status':'sealed',
               'summary':summary,'source_run':str(run),'source_table_digest':digest(before)})
    write_json(output/'checksums.json',{str(p.relative_to(output)):file_digest(p) for p in output.rglob('*') if p.is_file()})
    return summary
