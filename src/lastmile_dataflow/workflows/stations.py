"""Phase-three frozen scene → layered independent attempts → feedback and export."""
from dataclasses import asdict
from pathlib import Path
import itertools
import re
import time
import numpy as np
from ..io import read_json,write_json,digest,file_digest
from ..runtime.simulation import Simulation
from ..robots.action import InvalidAction
from ..scenes.geometry import body_points
from ..stations.sampling import coarse_samples,refinements,place_base,filter_station
from ..stations.execution import run_station_attempt,Budget,BudgetStop
from ..validation.stations import case1_verdict,audit_station_attempt


def run_station_map(snapshot,robot,config,collection,*,run_id,build=None,planner_factory=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',run_id): raise ValueError('unsafe station run id')
    snapshot=Path(snapshot).resolve(); root=Path(collection.output_dir); path=root/'station_maps'/run_id
    path.mkdir(parents=True,exist_ok=False); budget=Budget(config)
    frozen=read_json(snapshot/'version.json')
    if build:
        candidate=read_json(Path(build)/'task_candidate.json'); build_result=read_json(Path(build)/'result.json')
        if build_result['status']!='candidate_ready' or candidate['scene_version_id']!=frozen['version_id'] or candidate['target']!=config.target or candidate['case_type']!='case1': raise ValueError('build handoff mismatch/pending')
    config_packet={'schema_version':'3.0','robot':asdict(robot),'stations':asdict(config),'collection':asdict(collection),
                   'snapshot':str(snapshot),'scene_version':frozen['version_id'],'snapshot_checksums':read_json(snapshot/'checksums.json'),
                   'build':str(Path(build).resolve()) if build else None,'build_candidate_digest':file_digest(Path(build)/'task_candidate.json') if build else None,
                   'expected':'tested near station may succeed; source may be difficult; no global impossibility claim'}
    config_packet['input_digest']=digest(config_packet); write_json(path/'frozen_inputs.json',config_packet)
    sim=Simulation.from_snapshot(snapshot,robot,target=config.target)
    try:
        source_base=sim.robot.group('base').tolist(); target=sim.data.xpos[sim.target_id].copy()
        samples=coarse_samples(config,target,source_base)
        filters={}
        for sample in samples:
            try:
                place_base(sim,sample['base']); filters[sample['station_id']]=filter_station(sim,collection)
            except InvalidAction as exc:
                filters[sample['station_id']]={'status':'geometry_filtered','reason':'initial_joint_limit','diagnostic':str(exc)}
        write_json(path/'geometry_filters.json',filters)
        objects=[]
        for entry in sim.catalog:
            try:
                points=body_points(sim,entry['body_id'])
                objects.append({'body':entry['mjcf_body'],'min':points.min(axis=0).tolist(),'max':points.max(axis=0).tolist()})
            except ValueError: pass
        support=next((e['parent_instance_id'] for e in sim.catalog if e['instance_id']==config.target),None)
        write_json(path/'map_geometry.json',{'target_xyz':target.tolist(),'support':support,'objects':objects,
                  'note':'collision body envelopes for background only, not the planner collision geometry'})
    finally: sim.close()
    write_json(path/'candidates.json',samples)
    combos=list(itertools.product(config.arms,config.torso_heights,config.grasp_ids)); rows=[]
    def pending(sample,combo):
        side,h,gid=combo
        check=filters.get(sample['station_id'],{'status':'not_tested'})
        return {**sample,'arm':side,'torso_h':h,'grasp_row':gid,'approach_offset_m':config.approach_offset_m,'geometry':check['status'],'planning':'not_tested','execution':'not_executed','status':'geometry_filtered' if check['status']=='geometry_filtered' else 'not_tested','reason':'initial_collision_or_floor' if check['status']=='geometry_filtered' else 'not_scheduled','attempt':None}
    for sample in samples:
        rows.extend(pending(sample,combo) for combo in combos)
    write_json(path/'stations.json',rows)
    stopped=None; processed=0
    def collect(row):
        nonlocal processed
        if row['status']=='geometry_filtered': return
        budget.check()
        if budget.plans>=config.max_plans: raise BudgetStop('planning_budget')
        if budget.executions>=config.max_executions: raise BudgetStop('execution_budget')
        options={} if planner_factory is None else {'planner_factory':planner_factory}
        attempt_id=f'{run_id}-{row["station_id"]}-{row["arm"]}-h{row["torso_h"]:.4f}-g{row["grasp_row"]}'
        sample={k:row[k] for k in ('station_id','base','level')}
        trial=run_station_attempt(snapshot,robot,config,collection,sample,row['arm'],row['torso_h'],row['grasp_row'],root,attempt_id,budget,**options)
        layers=read_json(trial/'layers.json'); result=read_json(trial/'result.json')
        row.update({k:layers[k] for k in ('geometry','planning','execution')},status=result['status'],reason=result['termination_reason'],attempt=str(trial.resolve()))
        processed+=1; write_json(path/'stations.json',rows)
        write_json(path/'progress.json',{'processed':processed,'plans':budget.plans,'executions':budget.executions,'wall_time_s':time.monotonic()-budget.started})
        if result['status']=='infrastructure_error': raise RuntimeError('shared_planner_or_recording_infrastructure_failure')
        if result['status']=='budget_exhausted': raise BudgetStop(result['termination_reason'])
    try:
        # Spatial coverage first: test one configuration across stations before expanding arm/h.
        schedule=[row for combo in combos for row in rows if (row['arm'],row['torso_h'],row['grasp_row'])==combo]
        for row in schedule:
            collect(row)
            reserve=min(config.max_refinements,max(0,config.max_executions-2))
            if reserve and len(samples)<config.max_candidates and budget.executions >= config.max_executions-reserve and refinements(config,rows,samples):
                # Keep bounded physical budget for measured local boundaries, not more blind coarse poses.
                stopped='coarse_deferred_for_measured_local_refinement'
                break
        extra=refinements(config,rows,samples)
        extra=extra[:max(0,config.max_candidates-len(samples))]
        samples.extend(extra); write_json(path/'candidates.json',samples)
        new=[pending(s,c) for s in extra for c in combos]; rows.extend(new)
        for row in new: collect(row)
    except (BudgetStop,RuntimeError) as exc: stopped=str(exc)
    except KeyboardInterrupt: stopped='interrupted'
    for row in rows:
        if row['status']=='not_tested': row['reason']=stopped or 'not_scheduled'
    write_json(path/'stations.json',rows)
    audit={r['attempt']:audit_station_attempt(r['attempt']) for r in rows if r['attempt']}
    write_json(path/'audit.json',{'valid':all(a['valid'] for a in audit.values()),'attempts':audit})
    verdict=case1_verdict(rows,source_base)
    summary={'schema_version':'3.0','run_id':run_id,'source_base':source_base,'scene_version':frozen['version_id'],
             'successes':sum(r['execution']=='success' for r in rows),'failures':sum(r['execution']=='failure' for r in rows),
             'infrastructure_errors':sum(r['status']=='infrastructure_error' for r in rows),
             'planning_no_solution':sum(r['status']=='planning_no_solution' for r in rows),
             'geometry_filtered':sum(r['status']=='geometry_filtered' for r in rows),'not_tested':sum(r['status']=='not_tested' for r in rows),
             'case_condition':verdict,'stopped':stopped,'budget':{'plans':budget.plans,'executions':budget.executions,'wall_time_s':time.monotonic()-budget.started},
             'limits':{'plans':config.max_plans,'executions':config.max_executions,'wall_time_s':config.timeout_s},
             'data_collection_complete':False,'scope':'fixed-base case1 only; no navigation/VLA/multi-case claim'}
    summary['data_collection_complete']=bool(summary['successes'] and summary['failures'] and verdict['status']=='pass' and all(a['valid'] for a in audit.values()))
    write_json(path/'summary.json',summary)
    if build:
        from .build import record_feedback
        classification='infrastructure_failure' if summary['infrastructure_errors'] else 'success_without_expected_difficulty' if verdict['status']=='fail' else 'valid_unsolved'
        # Existing phase-two feedback vocabulary extended for verified case witnesses.
        if verdict['status']=='pass': classification='case_verified'
        feedback=record_feedback(build,classification,{'station_run':str(path.resolve()),'summary_sha256':file_digest(path/'summary.json'),'case_condition':verdict})
        write_json(path/'build_feedback.json',{'path':str(feedback)})
    from ..exporting.station_report import export_report
    export_report(path)
    write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
    return path


def _worker(connection,args,kwargs):
    try: connection.send({'path':str(run_station_map(*args,**kwargs))})
    except BaseException as exc: connection.send({'error_type':type(exc).__name__,'error':str(exc)})
    finally: connection.close()


def supervised_station_map(snapshot,robot,config,collection,*,run_id,build=None):
    import multiprocessing
    if not re.fullmatch(r'[A-Za-z0-9_-]+',run_id): raise ValueError('unsafe station run id')
    path=Path(collection.output_dir)/'station_maps'/run_id
    if path.exists(): raise FileExistsError(path)
    ctx=multiprocessing.get_context('spawn'); reader,writer=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_worker,args=(writer,(snapshot,robot,config,collection),{'run_id':run_id,'build':build}))
    start=time.monotonic()
    try:
        process.start(); writer.close(); process.join(config.timeout_s)
        if process.is_alive():
            process.terminate(); process.join(2)
            if process.is_alive(): process.kill(); process.join(2)
            path.mkdir(parents=True,exist_ok=True)
            # Partial actions remain on disk, not promoted to failure/success.
            write_json(path/'interruption.json',{'status':'budget_exhausted','reason':'hard_run_deadline','wall_time_s':time.monotonic()-start,'worker_reaped':not process.is_alive(),'partial_attempts':'incomplete, never physics failure'})
            return path
        if reader.poll():
            result=reader.recv()
            if 'path' in result: return Path(result['path'])
            raise RuntimeError(f"station worker {result['error_type']}: {result['error']}")
        raise RuntimeError('station worker exited without result')
    finally:
        if process.is_alive(): process.kill(); process.join(2)
        reader.close(); writer.close()
        if not process.is_alive(): process.close()
