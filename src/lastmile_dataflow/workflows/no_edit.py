"""Raw ProcTHOR collection, isolated GPU workers, incremental maps and resumable tasks."""
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
import fcntl
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import time
import uuid

from ..io import canonical,digest,file_digest,read_json,write_json


def scene_manifest(dataset_dir, houses=None):
    root=Path(dataset_dir).resolve()
    if root.name != 'procthor-10k-val': raise ValueError('no-edit collector requires procthor-10k-val')
    result=[]
    for path in root.glob('val_*.xml'):
        match=re.fullmatch(r'val_(\d+)\.xml',path.name)
        if not match: continue  # ceiling variants are NOT extra houses
        house=int(match.group(1)); metadata=root/f'val_{house}_metadata.json'
        if houses is not None and house not in houses: continue
        result.append({'house':house,'xml':str(path),'metadata':str(metadata),
                       'status':'ready' if metadata.is_file() else 'missing_metadata'})
    result.sort(key=lambda r:r['house'])
    if houses is not None and set(houses)-{r['house'] for r in result}:
        raise ValueError('requested houses missing from dataset')
    if not result: raise ValueError('no exported validation scenes found')
    return result


def available_gpus(ids, max_utilization=50, min_free_mb=10000):
    command=['nvidia-smi','--query-gpu=index,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits']
    output=subprocess.check_output(command,text=True,timeout=15)
    result=[]
    for line in output.splitlines():
        index,util,used,total=map(int,(v.strip() for v in line.split(',')))
        if index in ids and util <= max_utilization and total-used >= min_free_mb:
            result.append({'index':index,'utilization':util,'memory_used_mb':used,'free_mb':total-used})
    return sorted(result,key=lambda r:(r['utilization'],-r['free_mb']))


def check_disk(path, config):
    if shutil.disk_usage(path).free < config.min_free_disk_gb*1024**3:
        raise RuntimeError('disk_space_below_configured_reserve')


def task_seed(seed,house,task_id,station_id,trial):
    return int(digest([seed,house,task_id,station_id,trial])[:8],16) % (2**31-1)


def collect_task(baseline,task,assets_dir,config,collection,scene_path, *, max_trials=None):
    import mujoco
    import numpy as np
    from ..runtime.no_edit_execution import clone_sim,run_raw_attempt
    from ..stations.no_edit_sampling import disk_samples,initialize_station
    from ..stations.no_edit_statistics import aggregate,construction_rate,select_starts,success_goals
    from ..tasks.raw_scene import grasp_candidates
    from ..navigation.astar import scene_grid,search,task_support_obstacles
    from ..exporting.no_edit_heatmap import export_heatmap
    from ..exporting.navigation_map import export_navigation_map
    task_path=scene_path/'tasks'/task['task_id']; task_path.mkdir(parents=True,exist_ok=True)
    if (task_path/'result.json').exists():
        prior=read_json(task_path/'result.json')
        if prior['status'] not in ('incomplete','infrastructure_error'): return prior
    task = {**task, 'support_obstacles':task_support_obstacles(baseline,task)}
    write_json(task_path/'task.json',task)
    footprint=read_json(scene_path/'robot_geometry.json'); spacing=config.spacing_m or footprint['radius_m']
    anchor=task['anchor_world']; sigma=config.smoothing_sigma_m or spacing
    if (task_path/'stations.json').exists():
        stations=read_json(task_path/'stations.json')
    else:
        stations=[]; renderer=mujoco.Renderer(baseline.model,height=config.height,width=config.width)
        try:
            for station in disk_samples(anchor,config.radius_m,spacing):
                sim=clone_sim(baseline,target=task['target_body'])
                try:
                    row=initialize_station(sim,station,anchor,config,renderer=renderer,
                                           evidence_path=task_path/'initial_views'/station['station_id'])
                    stations.append(row)
                except ValueError as exc:
                    stations.append({**station,'geometry':'geometry_filtered','reason':str(exc)})
                finally: sim.close()
        finally: renderer.close()
        write_json(task_path/'stations.json',stations)
    candidates=grasp_candidates(task,assets_dir)
    write_json(task_path/'grasp_candidates.json',candidates)
    grid=scene_grid(baseline,anchor,config.radius_m,config.map_resolution_m,footprint['radius_m'],config.navigation_margin_m,
                    support_obstacles=task['support_obstacles'])
    # Heatmap uses ground obstacle geometry; navigation additionally inflates by the chassis radius.
    heat_grid=scene_grid(baseline,anchor,config.radius_m,config.map_resolution_m,0.,0.,support_obstacles=task['support_obstacles'])
    np.savez_compressed(task_path/'navigation_grid.npz',origin=grid.origin,resolution_m=grid.resolution,free=grid.free)
    export_navigation_map(task_path/'maps',grid,anchor,display_grid=heat_grid)
    trials=read_json(task_path/'trials.json') if (task_path/'trials.json').exists() else []
    rows=aggregate(stations,trials,config.trials_per_station)
    export_heatmap(task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,title=task['task_id']+' (initial coverage)')
    error=None; count=0
    for station in sorted(stations,key=lambda s:sum((s['xy'][i]-anchor[i])**2 for i in (0,1))):
        if station['geometry'] != 'valid': continue
        for trial in range(config.trials_per_station):
            completed=[r for r in trials if r['station_id']==station['station_id'] and r['trial']==trial
                       and r['status'] in ('success','failure','planning_no_solution')]
            if completed: continue
            check_disk(scene_path,config)
            attempt_id=task['task_id']+'-'+station['station_id']+f'-t{trial}-'+uuid.uuid4().hex[:8]
            seed=task_seed(config.seed,scene_path.name,task['task_id'],station['station_id'],trial)
            print(f'{scene_path.name}/{task["task_id"]}/{station["station_id"]} trial {trial+1}/{config.trials_per_station}',flush=True)
            row=run_raw_attempt(baseline,task,station,candidates,assets_dir,config,collection,scene_path,
                                attempt_id,seed=seed)
            row['trial']=trial; trials.append(row); count+=1
            write_json(task_path/'trials.json',trials)
            rows=aggregate(stations,trials,config.trials_per_station)
            write_json(task_path/'station_statistics.json',rows)
            write_json(task_path/'progress.json',{'terminal_trials':sum(r['terminal_trials'] for r in rows),
                       'expected_trials':sum(s['geometry']=='valid' for s in stations)*config.trials_per_station,
                       'last_attempt':row,'updated_at_utc':datetime.now(timezone.utc).isoformat()})
            if trial == config.trials_per_station-1 or row['status'] not in ('success','failure','planning_no_solution'):
                export_heatmap(task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,title=task['task_id'])
            if row['status']=='infrastructure_error': error='infrastructure_error'; break
            if row['status']=='incomplete': error='incomplete'; break
            if max_trials is not None and count >= max_trials: error='incomplete'; break
        if error: break
    rows=aggregate(stations,trials,config.trials_per_station)
    write_json(task_path/'station_statistics.json',rows)
    rate=construction_rate(rows,config.trials_per_station,config.discard_below)
    selection={'starts':[],'successful':[],'failed_rollouts':[]}
    successful=[]; status=error or rate['status']
    if status=='eligible':
        goals=success_goals(rows)
        connections={}
        def reachable(start):
            for goal in goals:
                if start['station_id']==goal['station_id']: continue
                path=search(grid,start['xy'],goal['xy'])
                if path: connections[(start['station_id'],goal['station_id'])]=path
            return any(key[0]==start['station_id'] for key in connections)
        starts=select_starts(rows,config.start_count,reachable=reachable)
        selection['starts']=starts
        for start in starts:
            ordered=[g for g in goals if (start['station_id'],g['station_id']) in connections]
            if config.max_s1_candidates: ordered=ordered[:config.max_s1_candidates]
            for goal in ordered:
                check_disk(scene_path,config)
                control=next(r['control'] for r in trials if r['station_id']==goal['station_id'] and r['status']=='success')
                path=connections[(start['station_id'],goal['station_id'])]
                attempt_id=task['task_id']+'-rollout-'+start['station_id']+'-'+goal['station_id']+'-'+uuid.uuid4().hex[:8]
                outcome=run_raw_attempt(baseline,task,start,candidates,assets_dir,config,collection,scene_path,attempt_id,
                        seed=control['seed'],path=path,goal=goal,winning_control=control)
                packet={**outcome,'start':start,'goal':goal,'path':path,
                        's0_failure_evidence':[r for r in trials if r['station_id']==start['station_id'] and r['status']!='success']}
                packet['s0_failure_videos']=[{'attempt':r['attempt'],'videos':r.get('videos'),
                    'third_person_video':r.get('third_person_video'),
                    'status':r.get('video_status','unknown'),'reason':r.get('reason'),
                    'attribution':r.get('attribution')} for r in packet['s0_failure_evidence']]
                if outcome['status']=='success' and outcome['navigation']['status']=='success':
                    successful.append(packet); selection['successful'].append(packet)
                    write_json(task_path/'selection.json',selection); break
                selection['failed_rollouts'].append(packet); write_json(task_path/'selection.json',selection)
                if outcome['status']=='infrastructure_error': error='infrastructure_error'; break
            if error: break
        status='retained' if len(successful) >= config.min_successful_rollouts else (error or 'no_successful_rollout')
    write_json(task_path/'selection.json',selection)
    export_heatmap(task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,selection=selection,title=task['task_id'])
    result={'schema_version':'no-edit-v1','task_id':task['task_id'],'instruction':task['instruction'],
            'operation':task['operation'],'status':status,'rate':rate,'successful_rollouts':len(successful),
            'minimum_rollouts':config.min_successful_rollouts,'preferred_rollouts':config.start_count,
            'dataset_eligible':status=='retained','collection_error':error,
            'heatmap':str(task_path/'maps/success_heatmap.png')}
    write_json(task_path/'result.json',result)
    return result


def run_scene(record,robot,config,collection,assets_dir,run_path, *, targets=None,max_tasks=None,max_trials=None):
    from ..scenes.source import SceneSource
    from ..runtime.simulation import Simulation
    from ..tasks.raw_scene import discover_tasks,robot_footprint
    scene_path=run_path/'scenes'/f'val_{record["house"]}'; scene_path.mkdir(parents=True,exist_ok=True)
    source=SceneSource.procthor(Path(record['xml']).parent,record['house'])
    if (scene_path/'scene/version.json').exists():
        sim=Simulation.from_snapshot(scene_path/'scene',robot)
    else:
        sim=Simulation.from_source(source,robot); sim.freeze(scene_path/'scene')
    try:
        check_disk(scene_path,config)
        write_json(scene_path/'source.json',source.provenance())
        write_json(scene_path/'robot_geometry.json',robot_footprint(sim))
        tasks,skipped=discover_tasks(sim,read_json(record['metadata']),config)
        for task in tasks: task['operation_base_mode']=config.operation_base_mode
        if targets: tasks=[t for t in tasks if t['instance_id'] in targets or t['asset_id'] in targets or t['task_id'] in targets]
        if max_tasks: tasks=tasks[:max_tasks]
        write_json(scene_path/'task_index.json',tasks); write_json(scene_path/'skipped_targets.json',skipped)
        results=[]
        for task in tasks:
            print(f'Begin {scene_path.name}/{task["task_id"]} {task["operation"]}',flush=True)
            result=collect_task(sim,task,assets_dir,config,collection,scene_path,max_trials=max_trials)
            results.append(result); write_json(scene_path/'task_results.json',results)
            if result['status']=='infrastructure_error' or result.get('collection_error')=='infrastructure_error': break
        status='infrastructure_error' if any(r['status']=='infrastructure_error' or r.get('collection_error')=='infrastructure_error' for r in results) else (
                'incomplete' if any(r['status']=='incomplete' for r in results) or len(results)<len(tasks) else 'completed')
        summary={'house':record['house'],'status':status,'tasks':len(tasks),'processed_tasks':len(results),
                 'retained_tasks':sum(r['dataset_eligible'] for r in results),
                 'successful_rollouts':sum(r['successful_rollouts'] for r in results)}
        write_json(scene_path/'summary.json',summary); return summary
    finally: sim.close()


def _scene_worker(record,robot,config,collection,assets_dir,run_path,gpu,options):
    # Must precede importing MuJoCo EGL / torch in this spawn process.
    os.environ['CUDA_VISIBLE_DEVICES']=str(gpu); os.environ['MUJOCO_GL']='egl'
    # EGL enumerates physical devices, independently of CUDA_VISIBLE_DEVICES.
    os.environ['MUJOCO_EGL_DEVICE_ID']=str(gpu); os.environ.setdefault('OMP_NUM_THREADS','2')
    scene_path=run_path/'scenes'/f'val_{record["house"]}'; scene_path.mkdir(parents=True,exist_ok=True)
    log=scene_path/'worker.log'
    with log.open('a',buffering=1) as stream:
        os.dup2(stream.fileno(),1); os.dup2(stream.fileno(),2)
        try:
            run_scene(record,robot,config,collection,assets_dir,run_path,**options)
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            write_json(scene_path/'summary.json',{'house':record['house'],'status':'infrastructure_error',
                       'error_type':type(exc).__name__,'message':str(exc),'gpu':gpu})


def rebuild_dataset_index(run_path):
    entries=[]
    for result_path in sorted((run_path/'scenes').glob('*/tasks/*/result.json')):
        result=read_json(result_path)
        if not result.get('dataset_eligible'): continue
        task=read_json(result_path.parent/'task.json'); selection=read_json(result_path.parent/'selection.json')
        for segment in selection['successful']:
            entries.append({'schema_version':'no-edit-v1','instruction':task['instruction'],'task':task,
                'attempt':segment['attempt'],'trajectory':segment['attempt']+'/trajectory.jsonl',
                'observations':segment['attempt']+'/observations.json','videos':segment['attempt']+'/videos.json',
                'third_person_video':segment.get('third_person_video'),
                's0':segment['start'],'s1':segment['goal'],'s0_failure_evidence':segment['s0_failure_evidence'],
                's0_failure_videos':segment.get('s0_failure_videos',[]),
                'operation_base_mode':segment.get('operation_base_mode',task.get('operation_base_mode','unknown')),
                'scene':str(result_path.parents[2]/'scene'),'heatmap':result['heatmap']})
    temporary=run_path/'.dataset_index.tmp'
    with temporary.open('wb') as stream:
        for entry in entries: stream.write(canonical(entry)+b'\n')
    os.replace(temporary,run_path/'dataset_index.jsonl')
    return len(entries)


def collect_batch(dataset_dir,assets_dir,robot,config,collection,*,run_id,gpu_ids,max_workers=2,
                  houses=None,resume=False,index_only=False,targets=None,max_tasks=None,max_trials=None):
    if config.planner_backend == 'curobo_v2_v080' and not index_only:
        from ..planning.curobo_v2 import verify_version
        verify_version()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',run_id): raise ValueError('unsafe run id')
    if max_workers < 1 or not gpu_ids: raise ValueError('workers/GPU list required')
    root=Path(collection.output_dir)/'no_edit'/run_id
    root.mkdir(parents=True,exist_ok=resume)
    lock=(root/'run.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    generation_start=datetime.now(timezone.utc);generation_monotonic=time.monotonic()
    records=scene_manifest(dataset_dir,houses)
    source_hash=digest({str(p.relative_to(Path(__file__).parents[1])):file_digest(p)
                       for p in sorted(Path(__file__).parents[1].rglob('*.py'))})
    frozen={'schema_version':'no-edit-v1','dataset_dir':str(Path(dataset_dir).resolve()),
            'assets_dir':str(Path(assets_dir).resolve()),'robot':asdict(robot),'config':asdict(config),
            'collection':asdict(collection),'code_sha256':source_hash,'scene_manifest':records,
            'subset':{'targets':targets,'max_tasks':max_tasks,'max_trials':max_trials}}
    frozen['input_digest']=digest(frozen)
    if resume and (root/'frozen_config.json').exists():
        if read_json(root/'frozen_config.json')['input_digest'] != frozen['input_digest']:
            raise ValueError('resume configuration/code mismatch: use a new run-id')
    else: write_json(root/'frozen_config.json',frozen)
    from ..recording.timing import GenerationTimer
    timer=GenerationTimer(root/'timing.json',resume=resume,started_at=generation_start,
                          started_monotonic=generation_monotonic)
    write_json(root/'scene_index.json',records)
    if index_only:
        write_json(root/'summary.json',{'status':'indexed_only','scenes':len(records),'no_physical_execution':True,
                   'generation_timing':timer.update('indexed_only',final=True)})
        lock.close(); return root
    pending=[]; completed={}
    for record in records:
        summary=root/'scenes'/f'val_{record["house"]}'/'summary.json'
        if resume and summary.exists() and read_json(summary)['status']=='completed':
            completed[record['house']]=read_json(summary)
        elif record['status']=='ready': pending.append(record)
        else: completed[record['house']]={'status':'missing_metadata','house':record['house']}
    ctx=multiprocessing.get_context('spawn'); active={}; stop=None
    options={'targets':targets,'max_tasks':max_tasks,'max_trials':max_trials}
    def interrupted(signum,frame):
        raise KeyboardInterrupt('signal '+str(signum))
    previous_signals={s:signal.signal(s,interrupted) for s in (signal.SIGTERM,signal.SIGINT)}
    try:
        while pending or active:
            check_disk(root,config)
            for gpu,job in list(active.items()):
                process,record,started=job
                if process.is_alive() and time.monotonic()-started > config.scene_timeout_s:
                    process.terminate(); process.join(10)
                    if process.is_alive(): process.kill(); process.join()
                    write_json(root/'scenes'/f'val_{record["house"]}'/'summary.json',
                               {'house':record['house'],'status':'incomplete','reason':'hard_scene_deadline'})
                if not process.is_alive():
                    process.join(); summary=root/'scenes'/f'val_{record["house"]}'/'summary.json'
                    result=read_json(summary) if summary.exists() else {'status':'infrastructure_error','reason':'worker_no_summary'}
                    completed[record['house']]=result; process.close(); del active[gpu]
                    # Worker infrastructure errors are isolated to this scene.
                    # Keep the evidence and continue dispatching the remaining scenes.
            if pending and len(active)<max_workers:
                devices=available_gpus(gpu_ids)
                write_json(root/'gpu_selection.json',{'available':devices,'active':list(active),
                           'policy':'lowest current utilization, then most free memory; <=50%, >=10000 MB free'})
                for device in devices:
                    gpu=device['index']
                    if gpu in active or len(active)>=max_workers or not pending: continue
                    record=pending.pop(0)
                    process=ctx.Process(target=_scene_worker,args=(record,robot,config,collection,assets_dir,root,gpu,options))
                    process.start(); active[gpu]=(process,record,time.monotonic())
                    print(f'GPU {gpu}: started val_{record["house"]} ({len(pending)} pending)',flush=True)
            retained=rebuild_dataset_index(root)
            status='running' if active or pending else (
                    'infrastructure_error' if any(r['status']=='infrastructure_error' for r in completed.values()) else (
                    'completed' if len(completed)==len(records) and all(r['status']=='completed' for r in completed.values()) else 'incomplete'))
            write_json(root/'summary.json',{'schema_version':'no-edit-v1','status':status,
                       'generation_timing':timer.update(status,final=not(active or pending)),
                       'scenes':len(records),'completed_scenes':len(completed),'pending_scenes':len(pending),
                       'active':{str(gpu):job[1]['house'] for gpu,job in active.items()},
                       'scene_results':completed,'retained_segments':retained,
                       'updated_at_utc':datetime.now(timezone.utc).isoformat()})
            if pending or active: time.sleep(10)
    except BaseException as exc:
        stop=type(exc).__name__+':'+str(exc)
        for process,record,started in active.values():
            if process.is_alive(): process.terminate(); process.join(10)
            if process.is_alive(): process.kill(); process.join()
        write_json(root/'interruption.json',{'status':'incomplete','reason':stop,'resume_at':'completed attempt boundaries only'})
        timing=timer.update('incomplete',final=True)
        summary=read_json(root/'summary.json') if (root/'summary.json').exists() else {}
        summary.update(status='incomplete',active={},generation_timing=timing,interruption_reason=stop)
        write_json(root/'summary.json',summary)
        raise
    finally:
        for signum,handler in previous_signals.items():signal.signal(signum,handler)
        lock.close()
    return root
