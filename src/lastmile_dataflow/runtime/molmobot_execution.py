"""Pure VLA station operation with actual stepping, evidence, and four real videos."""
from dataclasses import asdict
import gzip
from pathlib import Path
import re
import time
import traceback

import imageio.v2 as imageio
import mujoco
import numpy as np

from .episode_guard import isolated_episode, track_resource, mark_episode_phase, classify_episode_exception, episode_phase
from ..io import canonical, read_json, write_json
from ..recording.recorder import AttemptRecorder
from ..recording.head_views import head_frame
from ..recording.edit_views import ViewConfig
from ..recording.analysis_video import AnalysisVideo
from ..stations.no_edit_sampling import place_frozen_station
from ..validation.contacts import ContactStatistics, simulation_failure, SIMULATION_FAILURES, CONTACT_POLICY, contact_observations
from ..validation.pick import PickMonitor
from ..validation.no_edit import torso_tracking_error
from ..validation.molmobot import constraint, evaluate, PROTOCOL
from ..robots.action import InvalidAction
from ..exporting.no_edit_layout import attempt_group, index_attempt
from .no_edit_execution import clone_sim, navigate, OperationFailure
from types import SimpleNamespace


def instruction(task):
    # English task descriptions, as used by the model; no grasps/3D target pose input.
    name = re.sub(r'(?<!^)(?=[A-Z])', ' ', task.get('category') or 'object').lower()
    if task['operation'] == 'pick': return f'Pick up the {name}.'
    part = 'drawer' if task['joint_kind'] == 'slide' else 'door'
    return f'Open the {part} of the {name}.'


def first_point(sim, task):
    _, mask, visibility = head_frame(sim, task['target_body'], ViewConfig(width=1024,height=576))
    ys, xs = np.nonzero(mask)
    if not len(xs): raise ValueError('open_target_not_visible')
    # Pick a genuine visible pixel near mask centre, rather than bbox centre in empty space.
    distances = (xs-np.median(xs))**2+(ys-np.median(ys))**2
    index = int(np.argmin(distances))
    point = np.array([[xs[index]/1024., ys[index]/576.]],dtype=np.float32)
    return {task['instance_id']:{'head_camera':{'points':point,'num_points':np.array([1])}}}, mask


@isolated_episode
def run_vla_attempt(baseline, task, station, config, collection, root, attempt_id, *, seed, policy, path=None, goal=None):
    sim = clone_sim(baseline, target=task['target_body']); place_frozen_station(sim,station)
    if config.third_person_enabled: sim.enable_third_person()
    packet = {'schema_version':'molmobot-station-v1', 'robot':asdict(sim.robot.config),
        'collection':asdict(collection), 'task':task, 'station':station,
        'protocol':asdict(config), 'contact_policy':CONTACT_POLICY, 'seed':seed, 'policy':asdict(policy.config),
        'model_service':policy.metadata, 'navigation_path':path, 'navigation_goal':goal,
        'scene_version':read_json(root/'scene/version.json')['version_id']}
    recorder = AttemptRecorder(root,packet,attempt_group=attempt_group(task,station),
                               attempt_id=attempt_id,strategy='molmobot_station')
    write_json(recorder.path/'source.json',{'frozen_scene':str(root/'scene'),
        'source':sim.source.provenance(), 'no_scene_edits':True, 'initialization_only':station['base']})
    all_fingers = {sim.model.body(f'robot_0/ee_finger_{s}{i}').id for s in ('l','r') for i in (1,2)}
    monitors = {side:PickMonitor(sim,side,allowed_target_fingers=all_fingers) for side in ('left','right')}
    for monitor in monitors.values():
        monitor.initial.update(workspace_center_xy=task['anchor_world'][:2],workspace_radius_m=config.radius_m)
    samples = {side:[] for side in monitors}; contact_stats=ContactStatistics(); collisions=contact_stats.examples; replay=[]
    initial_height = float(sim.data.xpos[sim.target_id,2]); frames=None
    analysis = AnalysisVideo(task,station,radius_m=config.radius_m) if config.third_person_enabled else None
    physics = track_resource(gzip.open(recorder.path/'physics.jsonl.gz','wb'))
    actions = track_resource((recorder.path/'policy_actions.jsonl').open('xb'))
    requests = track_resource((recorder.path/'policy_requests.jsonl').open('xb'))
    status='infrastructure_error'; reason='unfinished'; verdict=None
    started=time.monotonic(); budget=config.attempt_timeout_s
    navigation={'status':'not_requested'}; phase='vla_operation'
    def state():
        measured = sim.observe_state()
        return {k:measured[k] for k in ('time_s','robot','target','nonfinite_fields') if k in measured}
    def video(frames):
        result=dict(frames)
        if analysis and 'third_person_camera' in frames:
            # Display only actual measured facts; no invented planned path.
            side=max(monitors,key=lambda s:len(samples[s][-1]['finger_forces_n']) if samples[s] else 0)
            result['third_person_camera']=analysis.frame(frames,phase=phase,time_s=float(sim.data.time),
                base_xy=sim.robot.group('base')[:2],target_height=float(sim.data.xpos[sim.target_id,2]),
                initial_height=initial_height,facts=samples[side][-1] if samples[side] else None,
                side=side,collisions=contact_stats.forbidden_contact_count)
        return result
    try:
        if path is not None:
            if goal is None: raise ValueError('navigation requires goal')
            phase='navigation'; navigation={'status':'running'}
            frames=sim.render(1024,576)
            recorder.observation(frames,step=0,time_s=float(sim.data.time))
            write_json(recorder.path/'initial_state.json',state())
            replay.append((float(sim.data.time),sim.data.qpos.copy()))
            recorder.video_frame(video(frames),float(sim.data.time))
            def nav_tick(action):
                nonlocal frames
                if time.monotonic()-started>=budget: raise TimeoutError('attempt_wall_clock_budget')
                before=state()
                def nav_check():
                    health=simulation_failure(sim)
                    contacts=contact_observations(sim,target_bodies=monitors['left'].target_bodies,fingers=all_fingers)
                    contact_stats.update(contacts)
                    physics.write(canonical({'time_s':float(sim.data.time),'phase':'navigation',
                                             'collisions':contacts,'issue':health})+b'\n')
                    return {'valid':not health,'issues':[health] if health else []}
                if not sim.started: sim.begin()
                command,stop=sim.step_policy(action,substep_check=nav_check)
                after=state(); replay.append((float(sim.data.time),sim.data.qpos.copy()))
                recorder.record_step(raw_action=np.asarray(action).tolist(),command=command,before=before,
                    after=after,check=stop or {'valid':True,'issues':[]},observations={'phase':'navigation'})
                frames=sim.render(1024,576); recorder.video_frame(video(frames),after['time_s'])
                if stop: raise OperationFailure(stop['issues'][0])
            ctx=SimpleNamespace(sim=sim,config=config,tick=nav_tick,
                yaw_gap=lambda a,b:float((a-b+np.pi)%(2*np.pi)-np.pi),
                hold_torso_target=lambda:float(station['initial']['torso'][0]))
            navigation=navigate(ctx,path,goal)
            delta=np.asarray(goal['base'])-sim.robot.group('base')
            delta[2]=ctx.yaw_gap(goal['base'][2],sim.robot.group('base')[2])
            if np.linalg.norm(delta[:2])>config.arrival_tolerance_m or abs(delta[2])>config.arrival_tolerance_rad:
                raise OperationFailure('arrival_tracking_error')
            write_json(recorder.path/'navigation.json',navigation)
            write_json(recorder.path/'operation_initial_state.json',state())
            phase='vla_operation'
        points=None
        if task['operation']=='open':
            points, mask=first_point(sim,task)
            imageio.imwrite(recorder.path/'target_mask.png',mask.astype(np.uint8)*255)
        frames=sim.render(1024,576)
        recorder.observation(frames,step=recorder.steps,time_s=float(sim.data.time))
        write_json(recorder.path/('operation_initial_state.json' if path is not None else 'initial_state.json'),state())
        write_json(recorder.path/'camera_profile.json',{'resolution':[1024,576],
            'camera_order':['wrist_camera_r','head_camera','wrist_camera_l'],
            'fovy_deg':{name:float(sim.model.cam_fovy[sim.model.camera(alias).id])
                        for name,alias in sim.robot.camera_names.items()},
            'extrinsic_jitter':False,'fisheye':'upstream image_warping_utils, once in model process'})
        if sim.third_person_camera:
            write_json(recorder.path/'third_person_initial.json',sim.third_person_camera.metadata())
        write_json(recorder.path/'analysis_video.json',{'style':'actual observer + wrist inset + measured facts',
            'camera':'third_person_camera','dimensions':[1280,720],'planned_path':None,
            'policy_input':False,'robot_videos':'raw 1024x576 RGB, pre-fisheye'})
        replay.append((float(sim.data.time),sim.data.qpos.copy()))
        mark_episode_phase('execution')
        for step in range(policy.config.task_horizon):
            remaining=budget-(time.monotonic()-started)
            if remaining<=0: raise TimeoutError('attempt_wall_clock_budget')
            observation={k:frames[k] for k in ('wrist_camera_r','head_camera','wrist_camera_l')}
            observation.update(qpos={k:np.asarray(v,dtype=np.float32) for k,v in sim.robot.state().items()},
                               task=instruction(task))
            if step==0 and points is not None: observation['object_image_points']=points
            response=policy.action(observation,task=task,attempt=attempt_id,seed=seed,first=step==0,timeout=remaining)
            # Persist predictions BEFORE validation/dispatch: rejected actions still
            # have raw, postprocessed, and input evidence, but no fake executed ctrl.
            requests.write(canonical({'step':step,'time_s':float(sim.data.time),
                **{k:v for k,v in response.items() if k not in ('processed_images','conditioning_points')}})+b'\n')
            requests.flush()
            # Model service verifies the open point survived upstream crop.
            if step==0:
                for i,pixels in enumerate(response.pop('processed_images') or []):
                    imageio.imwrite(recorder.path/f'model_input_{i}.png',pixels)
                write_json(recorder.path/'policy_instruction.json',{'instruction':instruction(task),
                    'target_conditioning':'one first-frame point' if points else 'language only',
                    'model_prompt':response.get('model_prompt'),
                    'original_conditioning_points':response.get('conditioning_points')})
            if time.monotonic()-started>=budget: raise TimeoutError('attempt_wall_clock_budget')
            action=response['action']; before=state()
            # Compose initial overlay before physics, with initial facts and clock.
            initial_video = video(frames) if step==0 else None
            failures=[]
            def check():
                health=simulation_failure(sim)
                if health:
                    failures.append(health)
                    physics.write(canonical({'time_s':float(sim.data.time),'issue':health,
                                             'state':state()})+b'\n')
                    return {'valid':False,'issues':[health]}
                mujoco.mj_forward(sim.model,sim.data)
                contacts=contact_observations(sim,target_bodies=monitors['left'].target_bodies,
                                        fingers=all_fingers)
                contact_stats.update(contacts)
                row={'time_s':float(sim.data.time),'collisions':contacts,'hands':{}}
                for side,monitor in monitors.items():
                    monitor.phase='vla_operation'
                    monitor.command_h=float(sim.data.ctrl[sim.robot.act_ids['link2_act']])
                    facts=monitor.sample()
                    facts.update(torso_tracking_error_rad=torso_tracking_error(facts),torso_error_policy='record_only')
                    if task['operation']=='open':
                        joint=sim.model.joint(task['joint_name']).id
                        facts['joint_value']=float(sim.data.qpos[sim.model.jnt_qposadr[joint]])
                    samples[side].append(facts); row['hands'][side]=facts
                    violation=constraint(facts,monitor.initial)
                    if violation: failures.append(violation)
                physics.write(canonical(row)+b'\n')
                return {'valid':not failures,'issues':sorted(set(failures))}
            if not sim.started: sim.begin()
            command,stop=sim.step_policy(action,substep_check=check)
            after=state(); replay.append((float(sim.data.time),sim.data.qpos.copy()))
            actions.write(canonical({'step':step,'time_s':before['time_s'],
                **{k:v for k,v in response.items() if k not in ('processed_images','conditioning_points')},
                'submitted_action':command['submitted_action'],'ctrl':command['ctrl']})+b'\n'); actions.flush()
            recorder.record_step(raw_action=response['raw_action'].tolist(),command=command,before=before,
                after=after,check=stop or {'valid':True,'issues':[]},observations={})
            # Only actual movement gets execution video. Initial frame + actual current frame.
            if step==0 and path is None: recorder.video_frame(initial_video,before['time_s'])
            frames=sim.render(1024,576); recorder.video_frame(video(frames),after['time_s'])
            if stop:
                reason=failures[0]
                status='simulation_failure' if reason in SIMULATION_FAILURES else 'failure'
                break
            if step%8==7 or step==policy.config.task_horizon-1:
                verdict=evaluate(task,samples,{s:m.initial for s,m in monitors.items()},config.open_hold_s)
                if verdict['status'] in ('success','infrastructure_error','simulation_failure'):
                    status=verdict['status']; reason=verdict['reason']; break
        else:
            status='failure'; reason='policy_step_budget_exhausted'
        if status=='infrastructure_error' and reason=='unfinished':
            status='failure'; reason='policy_step_budget_exhausted'
    except OperationFailure as exc:
        reason=exc.reason
        status='simulation_failure' if reason in SIMULATION_FAILURES else 'failure'
        if navigation['status'] in ('running','success') and phase=='navigation':
            navigation={'status':'failure','reason':reason}
    except InvalidAction as exc:
        status='failure'; reason='invalid_policy_action:'+str(exc)
        recorder.event('invalid_policy_action',message=str(exc))
    except TimeoutError as exc:
        status='timeout'; reason=str(exc)
        recorder.event('episode_timeout',message=reason)
    except KeyboardInterrupt:
        status='incomplete'; reason='interrupted'
        raise
    except Exception as exc:
        classified=classify_episode_exception(exc,episode_phase())
        exception_detail={**classified,'traceback':traceback.format_exc()}
        status=classified['status']; reason=classified['reason']
        recorder.event('exception',message=str(exc),error_type=type(exc).__name__)
    finally:
        if navigation['status']=='running': navigation={'status':'unknown','reason':reason}
        mark_episode_phase('finalize')
        physics.close(); actions.close(); requests.close()
        np.savez_compressed(recorder.path/'replay.npz',time_s=np.array([r[0] for r in replay]),
                            qpos=np.array([r[1] for r in replay]))
        write_json(recorder.path/'final_state.json',state())
        if sim.model_hash: sim.save_snapshot(recorder.path/'final.npz')
        outcome={'status':status,'reason':reason,'verdict':verdict,'protocol':PROTOCOL,
            # 'operation_backend':'molmobot','continuous_simulation':True,'navigation':navigation,
            'exception_detail':locals().get('exception_detail'),
            'operation_backend':'molmobot','continuous_simulation':True,'navigation':{'status':'not_requested'},
            'gripper_mode':policy.config.gripper_mode,'seed':seed,'elapsed_wall_s':time.monotonic()-started,
            'trajectory_metrics':contact_stats.metrics(task_success=status=='success' if status in ('success','failure') else None,
                simulation_valid=False if status=='simulation_failure' else True if sim.started else None),
            'contact_policy':CONTACT_POLICY,
            'attribution':{'category':'SimulationFailure' if status=='simulation_failure' else 'EpisodeTimeout' if status=='timeout' else 'PolicyFailure' if status=='failure' else 'Unknown',
                           'reason':reason,'collisions':collisions},
            'control':{'seed':seed,'policy':'MolmoBot-RBY1Multitask'}}
        write_json(recorder.path/'outcome.json',outcome)
        recorder.finish(status,reason,{'scene_validity':{'status':'valid','scope':'robot initialization'},
            'case_condition':{'status':'not_applicable'},
            'task_completion':{'status':status if status in ('success','failure') else 'unknown','reason':reason}},extra=outcome)
        sim.close()
    finalized=read_json(recorder.path/'result.json'); outcome['status']=finalized['status']
    media=read_json(recorder.path/'videos.json')
    return {**outcome,'station_id':station['station_id'],'attempt':str(recorder.path),
        'executed_steps':recorder.steps,'status_directory':index_attempt(recorder.path,outcome['status']),
        'videos':str(recorder.path/'videos.json'),
        'video_status':'not_executed' if not recorder.steps else 'recorded' if media['cameras'] else 'disabled',
        'third_person_video':str(recorder.path/'videos/third_person_camera.mp4') if 'third_person_camera' in media['cameras'] else None}
