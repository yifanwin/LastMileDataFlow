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
from ..runtime.episode_guard import execute_isolated_episode, isolated_recording
from ..exporting.no_edit_layout import assign_output_names,export_rollout


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


def sustained_policy_gpus(ids, policy):
    """All samples must pass; sort by worst utilization then minimum free memory."""
    samples=[]
    for i in range(policy.gpu_sample_count):
        samples.append({r['index']:r for r in available_gpus(ids,policy.max_gpu_utilization,policy.min_free_gpu_mb)})
        if i+1<policy.gpu_sample_count: time.sleep(policy.gpu_sample_interval_s)
    common=set(samples[0]).intersection(*(set(s) for s in samples[1:]))
    result=[]
    for index in common:
        rows=[s[index] for s in samples]
        result.append({'index':index,'utilization':max(r['utilization'] for r in rows),
            'free_mb':min(r['free_mb'] for r in rows),'samples':rows,
            'sample_window_s':(policy.gpu_sample_count-1)*policy.gpu_sample_interval_s})
    return sorted(result,key=lambda r:(r['utilization'],-r['free_mb']))


def check_disk(path, config):
    if shutil.disk_usage(path).free < config.min_free_disk_gb*1024**3:
        raise RuntimeError('disk_space_below_configured_reserve')


def task_seed(seed,house,task_id,station_id,trial):
    return int(digest([seed,house,task_id,station_id,trial])[:8],16) % (2**31-1)


def prepare_execution_stations(scene_path, task, config):
    """Bind immutable final points before loading an expensive VLA model."""
    task_path=scene_path/'tasks'/task['output_name']; task_path.mkdir(parents=True,exist_ok=True)
    station_input = None
    if config.augmented_variant or config.scene_package:
        if config.scene_package:
            from ..stations.scene_package import package_stations
            selected, station_input = package_stations(scene_path, task)
        else:
            from ..stations.augmented_input import execution_stations
            selected, station_input = execution_stations(scene_path, task)
        if (task_path/'stations.json').exists() and read_json(task_path/'stations.json') != selected:
            raise ValueError('cached stations differ from augmented final set; use new run-id')
        if (task_path/'trials.json').exists() and any(r['station_id'] not in station_input['station_ids']
                                                    for r in read_json(task_path/'trials.json')):
            raise ValueError('cached trials contain unselected augmented station')
        write_json(task_path/'stations.json', selected)
        write_json(task_path/'station_input.json', station_input)
        if not (task_path/'progress.json').exists():
            isolated_recording(scene_path,task['task_id'],'initial_progress',write_json,task_path/'progress.json', {'terminal_trials': 0,
                       'expected_trials': len(selected)*config.trials_per_station,
                       'execution_point_count': len(selected), 'station_input': 'verified_scene_dataset_set' if config.scene_package else 'verified_augmented_final_set',
                       'updated_at_utc': datetime.now(timezone.utc).isoformat()})


def collect_task(baseline,task,assets_dir,config,collection,scene_path, *, max_trials=None,policy=None,calibration_only=False):
    import mujoco
    import numpy as np
    from ..runtime.no_edit_execution import clone_sim,run_raw_attempt
    from ..stations.no_edit_sampling import disk_samples,initialize_station
    from ..stations.no_edit_statistics import aggregate,construction_rate,select_starts,success_goals,is_episode_terminal,is_episode_timeout
    from ..tasks.raw_scene import grasp_candidates
    from ..navigation.astar import scene_grid,search,task_support_obstacles
    from ..exporting.no_edit_heatmap import export_heatmap
    from ..exporting.navigation_map import export_navigation_map
    if 'output_name' not in task: assign_output_names([task])
    task_path=scene_path/'tasks'/task['output_name']; task_path.mkdir(parents=True,exist_ok=True)
    prepare_execution_stations(scene_path, task, config)
    if (task_path/'result.json').exists():
        prior=read_json(task_path/'result.json')
        resumable = ('incomplete','infrastructure_error','calibration_incomplete')
        advance = config.lastmile_selection and prior['status']=='calibration_complete' and not calibration_only
        if prior['status'] not in resumable and not advance: return prior
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
                    row=initialize_station(sim,station,anchor,config,renderer=renderer)
                    stations.append(row)
                except ValueError as exc:
                    stations.append({**station,'geometry':'geometry_filtered','reason':str(exc)})
                finally: sim.close()
        finally: renderer.close()
        write_json(task_path/'stations.json',stations)
    candidates=[] if config.operation_backend=='molmobot' else grasp_candidates(task,assets_dir)
    if config.operation_backend!='molmobot': write_json(task_path/'grasp_candidates.json',candidates)
    recording_errors=[]
    grid=scene_grid(baseline,anchor,config.radius_m,config.map_resolution_m,footprint['radius_m'],config.navigation_margin_m,
                    support_obstacles=task['support_obstacles'])
    # Heatmap uses ground obstacle geometry; navigation additionally inflates by the chassis radius.
    heat_grid=scene_grid(baseline,anchor,config.radius_m,config.map_resolution_m,0.,0.,support_obstacles=task['support_obstacles'])
    isolated_recording(scene_path,task['task_id'],'navigation_grid',np.savez_compressed,task_path/'navigation_grid.npz',origin=grid.origin,resolution_m=grid.resolution,free=grid.free,_diagnostics=recording_errors)
    isolated_recording(scene_path,task['task_id'],'navigation_map',export_navigation_map,task_path/'maps',grid,anchor,display_grid=heat_grid,_diagnostics=recording_errors)
    trials=read_json(task_path/'trials.json') if (task_path/'trials.json').exists() else []
    if config.lastmile_selection:
        from ..stations.lastmile_selection import recover_trial_records,accepted_trials
        trials=recover_trial_records(scene_path,task,stations,trials,config)
        accepted_trials(stations,trials,config.trials_per_station)
        write_json(task_path/'trials.json',trials)
    rows=aggregate(stations,trials,config.trials_per_station)
    isolated_recording(scene_path,task['task_id'],'initial_heatmap',export_heatmap,task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,title=task['task_id']+' (initial coverage)',_diagnostics=recording_errors)
    error=None; count=0
    for station in sorted(stations,key=lambda s:sum((s['xy'][i]-anchor[i])**2 for i in (0,1))):
        if station['geometry'] != 'valid': continue
        for trial in range(config.trials_per_station):
            completed=[r for r in trials if r['station_id']==station['station_id'] and r['trial']==trial
                       and is_episode_terminal(r)]
            if config.lastmile_selection:
                from ..stations.lastmile_selection import evaluable
                completed=[r for r in completed if evaluable(r)]
            if completed: continue
            check_disk(scene_path,config)
            attempt_id=task['operation']+'-'+task['output_name']+'-'+station['station_id']+f'-t{trial}-'+uuid.uuid4().hex[:8]
            seed=task_seed(config.seed,scene_path.name,task['task_id'],station['station_id'],trial)
            print(f'{scene_path.name}/{task["task_id"]}/{station["station_id"]} trial {trial+1}/{config.trials_per_station}',flush=True)
            if config.operation_backend=='molmobot':
                from ..runtime.molmobot_execution import run_vla_attempt
                row=execute_isolated_episode(run_vla_attempt,baseline,task,station,config,collection,scene_path,attempt_id,seed=seed,policy=policy,
                     _episode_context={'root':scene_path,'task':task,'station':station,'attempt_id':attempt_id})
            else:
                row=execute_isolated_episode(run_raw_attempt,baseline,task,station,candidates,assets_dir,config,collection,scene_path,
                                    attempt_id,seed=seed,_episode_context={'root':scene_path,'task':task,'station':station,'attempt_id':attempt_id})
            row={**row,'trial':trial}; trials.append(row); count+=1
            isolated_recording(scene_path,attempt_id,'trials',write_json,task_path/'trials.json',trials,_diagnostics=recording_errors)
            rows=aggregate(stations,trials,config.trials_per_station)
            isolated_recording(scene_path,attempt_id,'statistics',write_json,task_path/'station_statistics.json',rows,_diagnostics=recording_errors)
            isolated_recording(scene_path,attempt_id,'progress',write_json,task_path/'progress.json',{'terminal_trials':sum(r['terminal_trials'] for r in rows),
                       'completed_episodes':sum(r.get('completed_episodes',r['terminal_trials']) for r in rows),
                       'episode_errors':sum(r.get('episode_errors',0) for r in rows),
                       'timeout_trials':sum(r.get('timeout_trials',0) for r in rows),
                       'expected_trials':sum(s['geometry']=='valid' for s in stations)*config.trials_per_station,
                       'execution_point_count':sum(s['geometry']=='valid' for s in stations),
                       'last_attempt':row,'updated_at_utc':datetime.now(timezone.utc).isoformat()},_diagnostics=recording_errors)
            if trial == config.trials_per_station-1 or row['status'] not in ('success','failure','planning_no_solution'):
                isolated_recording(scene_path,attempt_id,'heatmap',export_heatmap,task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,title=task['task_id'],_diagnostics=recording_errors)
            if row['status']=='infrastructure_error': error='infrastructure_error'; break
            if row['status']=='incomplete' and not is_episode_timeout(row): error='incomplete'; break
            if max_trials is not None and count >= max_trials: error='incomplete'; break
        if error: break
    isolated_recording(scene_path,task['task_id'],'final_trials',write_json,task_path/'trials.json',trials,_diagnostics=recording_errors)
    rows=aggregate(stations,trials,config.trials_per_station)
    isolated_recording(scene_path,task['task_id'],'final_statistics',write_json,task_path/'station_statistics.json',rows,_diagnostics=recording_errors)
    rate=construction_rate(rows,config.trials_per_station,config.discard_below)
    selection={'starts':[],'successful':[],'failed_rollouts':[]}
    successful=[]; status=error or rate['status']
    choices = None
    if config.lastmile_selection:
        from ..stations.lastmile_selection import calibration_binding,station_calibration,recovery_choices
        binding=calibration_binding(scene_path,task,config)
        calibration=station_calibration(stations,trials,config.trials_per_station,binding,config)
        write_json(task_path/'calibration.json',calibration)
        choices=recovery_choices(stations,calibration,binding,config)
        rate={**rate,'status':'eligible' if calibration['complete'] else 'incomplete',
              'global_discard_applied':False,'complete_valid_trials':calibration['complete']}
        status=error or ('calibration_incomplete' if not calibration['complete'] else
                        'calibration_complete' if calibration_only else 'eligible' if choices else 'no_eligible_pair')
        if (task_path/'selection.json').exists():
            selection=read_json(task_path/'selection.json')
        from ..stations.lastmile_selection import recover_rollout_records
        selection=recover_rollout_records(scene_path,task,selection,trials,config)
        selection.update(binding=binding,calibration_sha256=file_digest(task_path/'calibration.json'),
                         pair_candidates=[{'s0':s['station_id'],'s1_candidates':[g['station_id'] for g in goals],
                                           'evidence':e} for s,goals,e in choices])
        successful=list(selection['successful'])
    if status=='eligible' and config.operation_backend!='molmobot':
        goals=success_goals(rows)
        connections={}
        def reachable(start):
            for goal in goals:
                if start['station_id']==goal['station_id']: continue
                path=search(grid,start['xy'],goal['xy'])
                if path: connections[(start['station_id'],goal['station_id'])]=path
            return any(key[0]==start['station_id'] for key in connections)
        if choices is None:
            starts=select_starts(rows,config.start_count,reachable=reachable)
            goals_by_start={s['station_id']:[g for g in goals if (s['station_id'],g['station_id']) in connections]
                            for s in starts}
        else:
            starts=[s for s,_,_ in choices]
            goals_by_start={s['station_id']:gs for s,gs,_ in choices}
        selection['starts']=starts
        for start in starts:
            ordered=goals_by_start[start['station_id']]
            if config.lastmile_selection:
                ordered=ordered[:config.max_s1_candidates or 5]
            elif config.max_s1_candidates:
                ordered=ordered[:config.max_s1_candidates]
            for goal in ordered:
                if config.lastmile_selection:
                    if any(p['start']['station_id']==start['station_id'] and
                           p['goal']['station_id']==goal['station_id'] for p in successful):
                        continue
                    prior=[p for p in selection['failed_rollouts'] if p['start']['station_id']==start['station_id']
                           and p['goal']['station_id']==goal['station_id']]
                    if any(p['status'] not in ('infrastructure_error','incomplete','recording_error','episode_error','simulation_failure','initialization_error') for p in prior):
                        continue
                    path=search(grid,start['xy'],goal['xy'])
                    if not path:
                        selection['failed_rollouts'].append({'start':start,'goal':goal,'status':'path_unavailable',
                                                            'reason':'astar_no_path','attempt':None,'path':None})
                        write_json(task_path/'selection.json',selection)
                        continue
                    connections[(start['station_id'],goal['station_id'])]=path
                check_disk(scene_path,config)
                control=next(r['control'] for r in reversed(trials) if r['station_id']==goal['station_id'] and r['status']=='success')
                path=connections[(start['station_id'],goal['station_id'])]
                attempt_id=task['operation']+'-'+task['output_name']+'-rollout-'+start['station_id']+'-'+goal['station_id']+'-'+uuid.uuid4().hex[:8]
                outcome=execute_isolated_episode(run_raw_attempt,baseline,task,start,candidates,assets_dir,config,collection,scene_path,attempt_id,
                        seed=control['seed'],path=path,goal=goal,winning_control=control,
                        _episode_context={'root':scene_path,'task':task,'station':start,'attempt_id':attempt_id})
                packet={**outcome,'start':start,'goal':goal,'path':path,
                        's0_failure_evidence':[r for r in trials if r['station_id']==start['station_id'] and r['status']!='success']}
                packet['s0_failure_videos']=[{'attempt':r['attempt'],'videos':r.get('videos'),
                    'third_person_video':r.get('third_person_video'),
                    'status':r.get('video_status','unknown'),'reason':r.get('reason'),
                    'attribution':r.get('attribution')} for r in packet['s0_failure_evidence']]
                if outcome['status']=='success' and outcome['navigation']['status']=='success':
                    successful.append(packet); selection['successful'].append(packet)
                    isolated_recording(scene_path,task['task_id'],'selection',write_json,task_path/'selection.json',selection,_diagnostics=recording_errors)
                    if not config.lastmile_selection: break
                else:
                    selection['failed_rollouts'].append(packet); isolated_recording(scene_path,task['task_id'],'selection',write_json,task_path/'selection.json',selection,_diagnostics=recording_errors)
                if outcome['status']=='infrastructure_error': error='infrastructure_error'; break
            if error: break
        status=error or ('retained' if len(successful) >= config.min_successful_rollouts else 'no_successful_rollout')
    if config.operation_backend=='molmobot' and not error and rate['status']!='incomplete':
        status='station_evaluation_complete' if rate['status']!='no_valid_stations' else 'no_valid_stations'
    if config.lastmile_selection:
        groups=[]
        for start, goals, evidence in choices or []:
            selected_goals=goals[:config.max_s1_candidates or 5]
            success_ids={p['goal']['station_id'] for p in successful
                         if p['start']['station_id']==start['station_id']}
            groups.append({'s0':start['station_id'],
                           's1_selected':[g['station_id'] for g in selected_goals],
                           'successful_s1':sorted(success_ids),
                           'successful_s1_count':len(success_ids),
                           'minimum_met':bool(success_ids),
                           'warnings':['single_successful_solution'] if len(success_ids)==1 else []})
        selection['start_groups']=groups
        selection['warnings']=(['fewer_than_preferred_starts'] if 0 < len(groups) < config.start_count else [])
    isolated_recording(scene_path,task['task_id'],'selection',write_json,task_path/'selection.json',selection,_diagnostics=recording_errors)
    isolated_recording(scene_path,task['task_id'],'final_heatmap',export_heatmap,task_path/'maps',heat_grid,rows,anchor,sigma,max_support_distance_m=config.smoothing_support_distance_m,selection=selection,title=task['task_id'],_diagnostics=recording_errors)
    result={'schema_version':'no-edit-v1','task_id':task['task_id'],'instruction':task['instruction'],
            'operation':task['operation'],'status':status,'rate':rate,'successful_rollouts':len(successful),
            'minimum_rollouts':config.min_successful_rollouts,'preferred_rollouts':config.start_count,
            'dataset_eligible':status=='retained','collection_error':error,
            'timeout_trials':sum(r.get('timeout_trials',0) for r in rows),
            'completed_episodes':sum(r.get('completed_episodes',r['terminal_trials']) for r in rows),
            'episode_errors':sum(r.get('episode_errors',0) for r in rows),
            'operation_backend':config.operation_backend,'station_only':config.operation_backend=='molmobot',
            'lastmile_selection':config.lastmile_selection,'calibration_only':calibration_only,
            'heatmap':str(task_path/'maps/success_heatmap.png')}
    if config.lastmile_selection:
        result['start_groups']=selection.get('start_groups',[])
        result['warnings']=selection.get('warnings',[])
    result['recording_errors']=list(recording_errors)
    result['recording_complete']=not recording_errors
    if recording_errors: result['dataset_eligible']=False
    isolated_recording(scene_path,task['task_id'],'task_result',write_json,task_path/'result.json',result,_diagnostics=recording_errors)
    result['recording_errors']=list(recording_errors)
    result['recording_complete']=not recording_errors
    if recording_errors: result['dataset_eligible']=False
    return result


def run_scene(record,robot,config,collection,assets_dir,run_path, *, targets=None,max_tasks=None,max_trials=None,calibration_only=False):
    if config.operation_backend == 'molmobot' and (
            robot.control_profile != 'molmobot-native-v1' or robot.control_hz != 10. or robot.physics_dt != .004):
        raise ValueError('MolmoBot requires explicit native motors and 100/20/4ms stepping')
    from ..scenes.source import SceneSource
    from ..runtime.simulation import Simulation
    from ..tasks.raw_scene import discover_tasks,robot_footprint
    scene_path=run_path/'scenes'/f'val_{record["house"]}'; scene_path.mkdir(parents=True,exist_ok=True)
    source=SceneSource.procthor(Path(record['xml']).parent,record['house'])
    if config.scene_package:
        from ..stations.scene_package import package_manifest, bind_package
        manifest = package_manifest(config.scene_package, robot, config)
        if manifest['scene_id'] != f'procthor-10k-val/val_{record["house"]}' or read_json(run_path/'frozen_config.json')['scene_dataset_input'] != manifest:
            raise ValueError('scene package frozen identity mismatch')
        version = read_json(Path(config.scene_package)/'scene/version.json')
        if file_digest(record['metadata']) != version['source']['metadata_sha256'] or file_digest(record['xml']) != version['source']['xml_sha256']:
            raise ValueError('scene package original source changed')
        bind_package(scene_path, manifest)
        # Hash-validated physical config is equal; use the frozen locator/JSON
        # representation so harmless symlink aliases do not break snapshot load.
        from ..config import RobotConfig
        robot = RobotConfig(**version['robot'])
    if config.augmented_variant:
        from ..stations.augmented_input import variant_manifest, bind_scene_inputs
        manifest = variant_manifest(config.augmented_variant, robot, config)
        if manifest['scene_id'] != f'procthor-10k-val/val_{record["house"]}':
            raise ValueError('augmented input scene identity mismatch')
        if read_json(run_path/'frozen_config.json')['augmented_input'] != manifest:
            raise ValueError('augmented input changed after run configuration freeze')
        version = read_json(Path(config.augmented_variant)/'scene/version.json')
        if file_digest(record['metadata']) != version['source']['metadata_sha256']:
            raise ValueError('augmented source metadata mismatch')
        bind_scene_inputs(scene_path, manifest)
    if (scene_path/'scene/version.json').exists():
        sim=Simulation.from_snapshot(scene_path/'scene',robot)
    else:
        sim=Simulation.from_source(source,robot); sim.freeze(scene_path/'scene')
    try:
        policy=None
        check_disk(scene_path,config)
        write_json(scene_path/'source.json',source.provenance())
        write_json(scene_path/'robot_geometry.json',robot_footprint(sim))
        tasks,skipped=discover_tasks(sim,read_json(record['metadata']),config)
        assign_output_names(tasks)
        for task in tasks: task['operation_base_mode']=config.operation_base_mode
        if targets: tasks=[t for t in tasks if t['instance_id'] in targets or t['asset_id'] in targets or t['task_id'] in targets]
        if config.augmented_variant or config.scene_package:
            source_task = read_json(scene_path/'scene_dataset_input/task.json' if config.scene_package else scene_path/'augmented_input/baseline/task.json')
            matches = [t for t in tasks if t['task_id'] == source_task['task_id']
                       and t['target_body'] == source_task['target_body']]
            if len(matches) != 1:
                raise ValueError('augmented target missing or excluded from discovered tasks')
            # Freeze the construction anchor/identity; keep discovered stable output name.
            tasks = [{**source_task, 'output_name': matches[0]['output_name'],
                      'operation_base_mode': config.operation_base_mode}]
        if max_tasks: tasks=tasks[:max_tasks]
        write_json(scene_path/'task_index.json',tasks); write_json(scene_path/'skipped_targets.json',skipped)
        for task in tasks:
            prepare_execution_stations(scene_path, task, config)
        if config.operation_backend=='molmobot' and tasks:
            from ..policies.molmobot import MolmoBotClient
            log_dir=Path(collection.output_dir)/'logs'; log_dir.mkdir(parents=True,exist_ok=True)
            write_json(scene_path/'model_service_status.json', {'status':'loading'})
            try:
                policy=MolmoBotClient(config.policy_config,log_dir/f'{run_path.name}-val-{record["house"]}-model.log')
            except BaseException as exc:
                write_json(scene_path/'model_service_status.json', {'status':'failed','reason':str(exc)})
                raise
            write_json(scene_path/'model_service.json',policy.metadata)
            write_json(scene_path/'model_service_status.json', {'status':'ready'})
        results=[]
        for task in tasks:
            print(f'Begin {scene_path.name}/{task["task_id"]} {task["operation"]}',flush=True)
            result=collect_task(sim,task,assets_dir,config,collection,scene_path,max_trials=max_trials,policy=policy,
                                calibration_only=calibration_only)
            results.append(result); write_json(scene_path/'task_results.json',results)
            if result['status']=='infrastructure_error' or result.get('collection_error')=='infrastructure_error': break
        status='infrastructure_error' if any(r['status']=='infrastructure_error' or r.get('collection_error')=='infrastructure_error' for r in results) else (
                'incomplete' if any(r['status'] in ('incomplete','calibration_incomplete') for r in results) or len(results)<len(tasks) else 'completed')
        summary={'house':record['house'],'status':status,'tasks':len(tasks),'processed_tasks':len(results),
                 'calibration_only':calibration_only,
                 'retained_tasks':sum(r['dataset_eligible'] for r in results),
                 'successful_rollouts':sum(r['successful_rollouts'] for r in results)}
        write_json(scene_path/'summary.json',summary); return summary
    finally:
        if 'policy' in locals() and policy is not None: policy.close()
        sim.close()


def _scene_worker(record,robot,config,collection,assets_dir,run_path,gpu,options):
    # Must precede importing MuJoCo EGL / torch in this spawn process.
    os.environ['CUDA_VISIBLE_DEVICES']=str(gpu); os.environ['MUJOCO_GL']='egl'
    # EGL enumerates physical devices, independently of CUDA_VISIBLE_DEVICES.
    os.environ['MUJOCO_EGL_DEVICE_ID']=str(gpu); os.environ.setdefault('OMP_NUM_THREADS','2')
    scene_path=run_path/'scenes'/f'val_{record["house"]}'; scene_path.mkdir(parents=True,exist_ok=True)
    log_dir=Path(collection.output_dir)/'logs'; log_dir.mkdir(parents=True,exist_ok=True)
    log=log_dir/f'{run_path.name}-val-{record["house"]}-worker.log'
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
    from ..exporting.canonical_index import rebuild_canonical_index
    rebuild_canonical_index(run_path)
    (run_path/'datasets').mkdir(exist_ok=True)
    entries=[]
    for result_path in sorted((run_path/'scenes').glob('*/tasks/*/result.json')):
        def index_inputs():
            result=read_json(result_path)
            if result.get('recording_complete') is False:
                return None
            return result,read_json(result_path.parent/'task.json'),read_json(result_path.parent/'selection.json')
        inputs=isolated_recording(result_path.parents[2],result_path.parent.name,'index_inputs',index_inputs)
        if inputs is None: continue
        result,task,selection=inputs
        for segment in selection['successful']:
            if segment['status'] != 'success' or segment['navigation']['status'] != 'success': continue
            sample=isolated_recording(result_path.parents[2],Path(segment['attempt']).name,'dataset_export',
                                  export_rollout,run_path,result_path.parents[2].name,task,segment,
                                  eligible=result.get('dataset_eligible',False))
            if sample is None: continue
            if result.get('dataset_eligible'):
                entries.append({'schema_version':'no-edit-index-v2','instruction':task['instruction'],
                    'task_id':task['task_id'],'operation':task['operation'],'attempt':segment['attempt'],
                    'sample':str(sample/'sample.json')})
    temporary=run_path/'.dataset_index.tmp'
    with temporary.open('wb') as stream:
        for entry in entries: stream.write(canonical(entry)+b'\n')
    os.replace(temporary,run_path/'dataset_index.jsonl')
    return len(entries)


def collect_batch(dataset_dir,assets_dir,robot,config,collection,*,run_id,gpu_ids,max_workers=2,
                  houses=None,resume=False,index_only=False,targets=None,max_tasks=None,max_trials=None,calibration_only=False):
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
    from ..validation.contacts import CONTACT_POLICY
    frozen={'contact_policy':CONTACT_POLICY,'schema_version':'no-edit-v1','dataset_dir':str(Path(dataset_dir).resolve()),
            'assets_dir':str(Path(assets_dir).resolve()),'robot':asdict(robot),'config':asdict(config),
            'collection':asdict(collection),'code_sha256':source_hash,'scene_manifest':records,
            'subset':{'targets':targets,'max_tasks':max_tasks,'max_trials':max_trials}}
    if config.augmented_variant:
        from ..stations.augmented_input import variant_manifest
        manifest = variant_manifest(config.augmented_variant, robot, config)
        if len(records) != 1 or manifest['scene_id'] != f'procthor-10k-val/val_{records[0]["house"]}':
            raise ValueError('augmented input requires exactly its source house')
        frozen['augmented_input'] = manifest
    if config.scene_package:
        from ..stations.scene_package import package_manifest
        manifest = package_manifest(config.scene_package, robot, config)
        if len(records) != 1 or manifest['scene_id'] != f'procthor-10k-val/val_{records[0]["house"]}':
            raise ValueError('scene package requires exactly its source house')
        frozen['scene_dataset_input'] = manifest
    policy_config=None
    if config.operation_backend=='molmobot':
        from ..policies.molmobot_config import load_policy_config,policy_manifest
        policy_config=load_policy_config(config.policy_config)
        frozen['model_manifest']=policy_manifest(policy_config)
        write_json(root/'model_manifest.json',frozen['model_manifest'])
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
        if resume and summary.exists() and read_json(summary)['status']=='completed' and not (
                config.lastmile_selection and read_json(summary).get('calibration_only') and not calibration_only):
            completed[record['house']]=read_json(summary)
        elif record['status']=='ready': pending.append(record)
        else: completed[record['house']]={'status':'missing_metadata','house':record['house']}
    ctx=multiprocessing.get_context('spawn'); active={}; stop=None
    options={'targets':targets,'max_tasks':max_tasks,'max_trials':max_trials,'calibration_only':calibration_only}
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
                devices=sustained_policy_gpus(gpu_ids,policy_config) if policy_config else available_gpus(gpu_ids)
                write_json(root/'gpu_selection.json',{'available':devices,'active':list(active),
                    'policy':({'mode':'sustained low utilization, then free memory',
                        'samples':policy_config.gpu_sample_count,'interval_s':policy_config.gpu_sample_interval_s,
                        'max_utilization':policy_config.max_gpu_utilization,
                        'min_free_mb':policy_config.min_free_gpu_mb} if policy_config else
                        'lowest current utilization, then most free memory; <=50%, >=10000 MB free')})
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
