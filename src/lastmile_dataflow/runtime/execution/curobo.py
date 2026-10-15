"""One cuRobo attempt: preparation, navigation, operation and result."""
from dataclasses import asdict
import time
import traceback
import numpy as np
from .lifecycle import isolated_episode, mark_episode_phase, classify_episode_exception, episode_phase
from ...io import read_json, write_json
from ..control.action import InvalidAction
from ...data.recorder import AttemptRecorder
from ...scenes.stations.sampling import place_frozen_station
from ..outcomes.evaluation import attribute
from ..outcomes.contacts import SIMULATION_FAILURES, CONTACT_POLICY
from .checks import EXECUTION_SEMANTICS, PickEvidenceComplete, resolve_timeout, timed

from .context import ContinuousContext, clone_sim, OperationFailure
from .navigation import navigate
from .manipulation import manipulate_v2 as manipulate

@isolated_episode
def run_curobo_attempt(baseline,task,station,candidates,assets_dir,config,collection,*,root,attempt_id,seed,path=None,goal=None,winning_control=None):
    sim=clone_sim(baseline,target=task['target_body']); place_frozen_station(sim,station)
    if config.third_person_enabled: sim.enable_third_person()
    # One live recorder; optional diagnostic streams share this rollout.
    packet={'execution_semantics_version':EXECUTION_SEMANTICS,'robot':asdict(sim.robot.config),'collection':asdict(collection),
            'task':task,'station':station,'protocol':asdict(config),'contact_policy':CONTACT_POLICY,'seed':seed,'path':path,'goal':goal,
            'scene':getattr(baseline, 'scene_id', None)}
    recorder=AttemptRecorder(root,packet,attempt_id=attempt_id)
    recorder.bind_simulation(sim, width=config.width, height=config.height)
    ctx=ContinuousContext(sim,recorder,config)
    if config.third_person_enabled:
        from ...data.rendering.analysis_video import AnalysisVideo
        from ...scenes.tasks.discovery import robot_footprint
        footprint=robot_footprint(sim)
        ctx.analysis=AnalysisVideo(task,station,path=path,goal=goal,radius_m=config.radius_m,
                                   footprint_radius_m=footprint['radius_m'])
        write_json(recorder.path/'analysis_video.json',{'camera':'third_person_camera',
            'style':'observer + synchronized wrist inset + measured facts + planned/actual path',
            'station_footprint':footprint,'station_circle_inflation_m':0.,
            'dimensions':[1280,720],'playback_speed':1.,'raw_robot_cameras_unchanged':True,
            'third_person_png':'raw observer RGB','overlays':'actual simulation state; unmeasured contact shown as --'})
    write_json(recorder.path/'initial_state.json',sim.observe_state())
    mark_episode_phase('execution')
    status='infrastructure_error'; reason='unfinished'; control=None
    if task['operation']=='open': ctx.max_plans=config.max_open_plans
    navigation={'status':'not_requested'}; attribution=None; retries=[]
    execution_termination=None; task_outcome=None; verdict=None; exception_detail=None
    try:
        if path: navigation=navigate(ctx,path,goal)
        ctx.plan_count=0
        for retry in range((config.operation_retries if path else 0)+1):
            try:
                verdict,control=manipulate(ctx,task,candidates,assets_dir,seed+retry,winning_control)
                status,reason=verdict['status'],verdict['reason']
                if status == 'success': break
                raise OperationFailure(reason)
            except OperationFailure as exc:
                attribution=attribute(exc.reason,samples=ctx.samples,collisions=ctx.collisions,diagnostic=exc.diagnostic)
                reason=exc.reason
                status='simulation_failure' if reason in SIMULATION_FAILURES else 'planning_no_solution' if not recorder.steps else 'failure'
                retries.append({'retry':retry,'reason':reason,'attribution':attribution})
                if retry >= (config.operation_retries if path else 0) or status=='simulation_failure': break
                initial=read_json(recorder.path/'initial_state.json')['target']['pose_world_xyz_wxyz']
                if np.linalg.norm(sim.data.xpos[sim.target_id]-np.asarray(initial[:3])) > .01: break
                ctx.monitor=None; ctx.phase='retry_release'
                for _ in range(10):
                    a=sim.robot.neutral_action(); a[10]=a[18]=-.05; ctx.tick(a,fixed_base=False)
                ctx.plan_count=0
        if status != 'success' and attribution is None:
            attribution=attribute(reason,samples=ctx.samples,collisions=ctx.collisions)
    except OperationFailure as exc:
        reason=exc.reason
        status='simulation_failure' if reason in SIMULATION_FAILURES else 'failure' if recorder.steps else 'planning_no_solution'
        if ctx.phase in ('navigation','arrival'): navigation={'status':'failure','reason':reason}
        attribution=attribute(reason,samples=ctx.samples,collisions=ctx.collisions,diagnostic=exc.diagnostic)
    except InvalidAction as exc:
        # An illegal command is an isolated trial failure. Do not continue this
        # simulation, retry an unsafe command, or abort other independent points.
        status='failure'; reason='control_limit:'+str(exc)
        attribution=attribute(reason,samples=ctx.samples,collisions=ctx.collisions)
        recorder.event('control_limit_warning',phase=ctx.phase,error_type='InvalidAction',message=str(exc))
        print(f'WARNING {attempt_id}: {reason}; skipping this attempt, continuing sampling',flush=True)
        if ctx.phase in ('navigation','arrival'): navigation={'status':'failure','reason':reason}
    except PickEvidenceComplete as exc:
        task_outcome=exc.verdict
        status='success'; reason=task_outcome['reason']; control=ctx.active_control
        recorder.event('physical_pick_success',phase=ctx.phase,task_outcome=task_outcome)
    except TimeoutError as exc:
        execution_termination,task_outcome=resolve_timeout(ctx,task,str(exc))
        status=task_outcome['status'] if task_outcome['status'] in ('success','infrastructure_error','simulation_failure') else 'timeout'
        reason=task_outcome['reason'] if status=='success' else str(exc)
        control=ctx.active_control or control
        recorder.event('episode_timeout',message=str(exc),task_outcome=task_outcome)
    except Exception as exc:
        classified=classify_episode_exception(exc,episode_phase())
        exception_detail={**classified,'traceback':traceback.format_exc()}
        status=classified['status']; reason=classified['reason']
        recorder.event('exception',phase=ctx.phase,error_type=type(exc).__name__,message=str(exc))
    finally:
        mark_episode_phase('finalize')
        with timed(ctx, 'context_finalize'):
            ctx.finish()
        if execution_termination is None:
            execution_termination={'status':'completed' if status in ('success','failure','planning_no_solution') else status,'reason':reason,'phase':ctx.phase}
        if task_outcome is None:
            observed=verdict
            task_outcome=observed if observed and observed.get('status')==status else None
            task_outcome=task_outcome or {'status':status if status in ('success','failure','simulation_failure') else 'unknown','reason':reason}
        diagnostics={'schema_version':EXECUTION_SEMANTICS,'wall_elapsed_s':time.monotonic()-ctx.started_at,
                     'attempt_wall_budget_s':config.attempt_timeout_s,
                     'simulation_elapsed_s':float(sim.data.time)-ctx.initial_sim_time,
                     'timings':ctx.timings, 'timing_scope':'context lifetime; simulation includes physics monitor/trace; initialization excluded; recorder finalization reported separately'}
        write_json(recorder.path/'execution_diagnostics.json',diagnostics)
        result={'execution_semantics_version':EXECUTION_SEMANTICS,'execution_termination':execution_termination,
                'task_outcome':task_outcome,'execution_diagnostics':diagnostics,
                'status':status,'reason':reason,'attribution':attribution,'control':control,
                'exception_detail':exception_detail,
                'navigation':navigation,'retry_history':retries,'continuous_simulation':True,
                'operation_base_mode':config.operation_base_mode,
                'planner_backend':config.planner_backend,
                'manipulation_protocol':'mobile-pick-v2' if task['operation']=='pick' else 'mobile-open-v2',
                'torso_error_policy':config.torso_error_policy, 'contact_policy':CONTACT_POLICY,
                'trajectory_metrics':ctx.contact_stats.metrics(
                    task_success=task_outcome['status']=='success' if task_outcome['status'] in ('success','failure') else None,
                    simulation_valid=False if status=='simulation_failure' else True if sim.started else None)}
        finish_started=time.monotonic()
        recorder.finish(status, reason)
        diagnostics['recorder_finalize_wall_s']=time.monotonic()-finish_started
        diagnostics['wall_including_recorder_finalize_s']=time.monotonic()-ctx.started_at
    result['status'] = recorder.result['status']
    return {**result, 'station_id': station['station_id'], 'attempt': str(recorder.path),
            'rollout_id': recorder.attempt_id, 'seed': seed, 'executed_steps': recorder.steps,
            'recording_complete': recorder.result['recording_complete']}

