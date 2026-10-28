"""Shared VLA rollout with embodiment observations and actual physical stepping."""
from dataclasses import asdict
import time
import traceback

import imageio.v2 as imageio
import mujoco
import numpy as np

from .lifecycle import isolated_episode, track_resource, mark_episode_phase, classify_episode_exception, episode_phase
from ...utils.logging_config import trial_progress
from ...utils.io import canonical, write_json
from ...data.recorder import AttemptRecorder, NullTrace
from ...data.rendering.head_views import head_frame
from ...data.rendering.edit_views import ViewConfig
from ...data.rendering.analysis_video import AnalysisVideo
from ...policies.task import instruction
from ...scenes.stations.sampling import place_frozen_station
from ..outcomes.contacts import ContactSampler, ContactStatistics, NavigationContacts, simulation_failure, SIMULATION_FAILURES, CONTACT_POLICY, contact_observations
from ..outcomes.pick import PickMonitor
from ..outcomes.evaluation import torso_tracking_error
from ..outcomes.vla import constraint, PROTOCOL, TaskEvidence
from ..control.action import InvalidAction
from .context import clone_sim, OperationFailure
from .navigation import navigate
from types import SimpleNamespace


def first_point(sim, task):
    _, mask, visibility = head_frame(sim, task['target_body'], ViewConfig(width=1024,height=576))
    ys, xs = np.nonzero(mask)
    if not len(xs): raise ValueError('open_target_not_visible')
    # Pick a genuine visible pixel near mask centre, rather than bbox centre in empty space.
    distances = (xs-np.median(xs))**2+(ys-np.median(ys))**2
    index = int(np.argmin(distances))
    point = np.array([[xs[index]/1024., ys[index]/576.]],dtype=np.float32)
    return {task['instance_id']:{'head_camera':{'points':point,'num_points':np.array([1])}}}, mask


class OperationEvidence:
    """Per-physics-tick facts, constraint checks and the streaming task verdict of one rollout."""

    def __init__(self, sim, task, config, physics, *, phase):
        self.sim, self.task, self.physics, self.phase = sim, task, physics, phase
        self.record_physics = not isinstance(physics, NullTrace)   # skip per-tick JSON nobody keeps
        self.fingers = {body for side in sim.robot.arms for group in sim.robot.finger_bodies(side) for body in group}
        self.monitors = {side:PickMonitor(sim,side,allowed_target_fingers=self.fingers) for side in sim.robot.arms}
        self.primary = next(iter(self.monitors.values()))
        for monitor in self.monitors.values():
            monitor.initial.update(workspace_center_xy=task['anchor_world'][:2],workspace_radius_m=config.radius_m)
        self.samples = {side:[] for side in self.monitors}
        self.contact_stats = ContactStatistics()
        self.sampler = ContactSampler(sim)
        self.tasks = TaskEvidence(task, {s:m.initial for s,m in self.monitors.items()}, config.open_hold_s)
        self.failures = []

    def check(self):
        sim, task, failures = self.sim, self.task, self.failures
        health=simulation_failure(sim)
        if health:
            failures.append(health)
            if self.record_physics:
                self.physics.write(canonical({'time_s':float(sim.data.time),'issue':health,
                                              'state':sim.observe_state()})+b'\n')
            return {'valid':False,'issues':[health]}
        mujoco.mj_forward(sim.model,sim.data)
        # One contact pass per tick, shared by telemetry and every hand monitor.
        frame=self.sampler.capture(target_bodies=self.primary.target_bodies,fingers=self.fingers)
        contacts=frame.observations()
        self.contact_stats.update(contacts)
        row={'time_s':float(sim.data.time),'collisions':contacts,'hands':{},'ctrl':sim.data.ctrl.tolist()}
        for side,monitor in self.monitors.items():
            monitor.phase=self.phase
            monitor.command_h=sim.robot.control_height()
            facts=monitor.sample(frame)
            if 'torso' in facts:
                facts['torso_tracking_error_rad'] = torso_tracking_error(facts)
            else:
                facts['lift_tracking_error_m'] = abs(facts['lift']-sim.robot.control_height())
            facts['torso_error_policy'] = 'record_only'
            if task['operation']=='open':
                joint=sim.model.joint(task['joint_name']).id
                facts['joint_value']=float(sim.data.qpos[sim.model.jnt_qposadr[joint]])
            self.samples[side].append(facts); row['hands'][side]=facts
            self.tasks.add(side, facts)
            violation=constraint(facts,monitor.initial)
            if violation: failures.append(violation)
        if self.record_physics:
            self.physics.write(canonical(row)+b'\n')
        return {'valid':not failures,'issues':sorted(set(failures))}

    def verdict(self):
        return self.tasks.verdict()


@isolated_episode
def run_vla_attempt(baseline, task, station, config, collection, *, root, attempt_id, seed, policy, path=None, goal=None):
    sim = clone_sim(baseline, target=task['target_body'], handle=task.get('handle_body')); place_frozen_station(sim,station)
    pi05 = config.operation_backend == 'pi05'
    width,height = (config.width,config.height) if pi05 else (1024,576)
    def policy_step(action, **kwargs):
        return (sim.step if pi05 else sim.step_policy)(action, **kwargs)
    if config.third_person_enabled: sim.enable_third_person()
    packet = {'schema_version':'pi05-station-v1' if pi05 else 'molmobot-station-v1', 'robot':asdict(sim.robot.config),
        'collection':asdict(collection), 'task':task, 'station':station,
        'protocol':asdict(config), 'contact_policy':CONTACT_POLICY, 'seed':seed, 'policy':asdict(policy.config),
        'model_service':policy.metadata, 'navigation_path':path, 'navigation_goal':goal,
        'scene':getattr(baseline, 'scene_id', None)}
    recorder = AttemptRecorder(root,packet,attempt_id=attempt_id)
    recorder.bind_simulation(sim, width=width, height=height)
    contact_sampler = ContactSampler(sim)
    navigation_contacts = NavigationContacts()
    initial_height = float(sim.data.xpos[sim.target_id,2]); frames=None
    analysis = AnalysisVideo(task,station,radius_m=config.radius_m) if config.third_person_enabled and collection.video_cameras is None else None
    from ...data.recorder import open_trace
    physics = track_resource(open_trace(recorder.path/'physics.jsonl.gz', collection.record_physics))
    evidence = OperationEvidence(sim, task, config, physics, phase='vla_operation')
    all_fingers, monitors, primary_monitor = evidence.fingers, evidence.monitors, evidence.primary
    samples, contact_stats = evidence.samples, evidence.contact_stats; collisions=contact_stats.examples
    actions = track_resource(open_trace(recorder.path/'policy_actions.jsonl.gz', collection.record_physics))
    requests = track_resource(open_trace(recorder.path/'policy_requests.jsonl.gz', collection.record_physics))
    status='infrastructure_error'; reason='unfinished'; verdict=None; exception_detail=None
    started=time.monotonic(); budget=config.attempt_timeout_s
    navigation={'status':'not_requested'}; phase='vla_operation'
    def state():
        return sim.observe_state()
    def video(frames):
        result=dict(frames)
        if analysis and 'third_person_camera' in frames:
            # Display only actual measured facts; no invented planned path.
            side=max(monitors,key=lambda s:len(samples[s][-1]['finger_forces_n']) if samples[s] else 0)
            result['third_person_camera']=analysis.frame(frames,phase=phase,time_s=float(sim.data.time),
                base_xy=sim.robot.group('base')[:2],target_height=float(sim.data.xpos[sim.target_id,2]),
                initial_height=initial_height,facts=samples[side][-1] if samples[side] else None,
                side=side,collisions=contact_stats.forbidden_contact_count,
                wrist_camera='robot0_eye_in_hand' if sim.robot.config.name=='panda_omron' else None)
        return result
    try:
        if path is not None:
            if goal is None: raise ValueError('navigation requires goal')
            phase='navigation'; navigation={'status':'running'}
            recorder.phase=phase
            sim.begin()
            frames=sim.render(width,height)
            write_json(recorder.path/'initial_state.json',state())
            initial_navigation_video=video(frames)
            def nav_tick(action):
                nonlocal frames
                if time.monotonic()-started>=budget: raise TimeoutError('attempt_wall_clock_budget')
                recorder.phase=ctx.phase
                def nav_check():
                    health=simulation_failure(sim)
                    if ctx.navigation_contacts.observe(sim, contact_sampler, config.navigation_max_penetration_m):
                        health = 'navigation_severe_collision'
                    contacts=contact_observations(sim,target_bodies=primary_monitor.target_bodies,fingers=all_fingers)
                    contact_stats.update(contacts)
                    physics.write(canonical({'time_s':float(sim.data.time),'phase':'navigation',
                                             'collisions':contacts,'issue':health})+b'\n')
                    return {'valid':not health,'issues':[health] if health else []}
                if not sim.started: sim.begin()
                command,stop=policy_step(action,substep_check=nav_check)
                if recorder.steps == 1:
                    recorder.video_frame(initial_navigation_video)
                frames=sim.render(width,height); recorder.video_frame(video(frames))
                if stop: raise OperationFailure(stop['issues'][0])
            ctx=SimpleNamespace(sim=sim,config=config,tick=nav_tick,navigation_contacts=navigation_contacts,
                yaw_gap=lambda a,b:float((a-b+np.pi)%(2*np.pi)-np.pi),
                hold_torso_target=lambda:sim.robot.control_height())
            mark_episode_phase('execution')
            navigation=navigate(ctx,path,goal)
            delta=np.asarray(goal['base'])-sim.robot.group('base')
            delta[2]=ctx.yaw_gap(goal['base'][2],sim.robot.group('base')[2])
            if np.linalg.norm(delta[:2])>config.arrival_tolerance_m or abs(delta[2])>config.arrival_tolerance_rad:
                raise OperationFailure('arrival_tracking_error')
            write_json(recorder.path/'navigation.json',navigation)
            write_json(recorder.path/'operation_initial_state.json',state())
            phase='vla_operation'
        points=None
        if task['operation']=='open' and not pi05:
            points, mask=first_point(sim,task)
            imageio.imwrite(recorder.path/'target_mask.png',mask.astype(np.uint8)*255)
        recorder.phase=phase
        if not sim.started: sim.begin()
        frames=sim.render(width,height)
        write_json(recorder.path/('operation_initial_state.json' if path is not None else 'initial_state.json'),state())
        write_json(recorder.path/'camera_profile.json',{'resolution':[width,height],
            'camera_order':list(sim.robot.camera_names) if pi05 else ['wrist_camera_r','head_camera','wrist_camera_l'],
            'fovy_deg':{name:float(sim.model.cam_fovy[sim.model.camera(alias).id])
                        for name,alias in sim.robot.camera_names.items()},
            'extrinsic_jitter':False,'fisheye':False if pi05 else 'upstream image_warping_utils, once in model process'})
        if sim.third_person_camera:
            write_json(recorder.path/'third_person_initial.json',sim.third_person_camera.metadata())
        if analysis is not None:
            write_json(recorder.path/'analysis_video.json',{'style':'actual observer + wrist inset + measured facts',
                'camera':'third_person_camera','dimensions':[1280,720],'planned_path':None,
                'policy_input':False,'robot_videos':f'raw {width}x{height} RGB'})
        mark_episode_phase('execution')
        for step in range(policy.config.task_horizon):
            remaining=budget-(time.monotonic()-started)
            if remaining<=0: raise TimeoutError('attempt_wall_clock_budget')
            if pi05:
                from ...policies.pi05.observation import observation as pi05_observation
                observation = pi05_observation(sim,frames,instruction(task))
            else:
                observation={k:frames[k] for k in ('wrist_camera_r','head_camera','wrist_camera_l')}
                observation.update(qpos={k:np.asarray(v,dtype=np.float32) for k,v in sim.robot.state().items()},
                                   task=instruction(task))
            if step==0 and points is not None: observation['object_image_points']=points
            # The RPC deadline is the fixed policy timeout, NOT the remaining attempt
            # budget: the budget is checked again right after the reply arrives.
            response=policy.action(observation,task=task,attempt=attempt_id,seed=seed,first=step==0)
            recorder.phase=phase
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
                    'data_layer':'derived', 'source_frame_id':0, 'kind':'actual_policy_processed_input',
                    'target_conditioning':'one first-frame point' if points else 'language only',
                    'model_prompt':response.get('model_prompt'),
                    'original_conditioning_points':response.get('conditioning_points')})
            if time.monotonic()-started>=budget: raise TimeoutError('attempt_wall_clock_budget')
            action=response['action']; before=float(sim.data.time)
            if pi05:
                from ...policies.pi05.actions import execution_action
                action = execution_action(sim.robot, action)
            # Compose initial overlay before physics, with initial facts and clock.
            initial_video = video(frames) if step==0 else None
            failures=evidence.failures=[]
            check=evidence.check
            if not sim.started: sim.begin()
            command,stop=policy_step(action,substep_check=check,command_raw=response['raw_action'])
            actions.write(canonical({'step':step,'time_s':before,
                **{k:v for k,v in response.items() if k not in ('processed_images','conditioning_points')},
                'submitted_action':command['submitted_action'],'ctrl':command['ctrl']})+b'\n'); actions.flush()
            # Only actual movement gets execution video. Initial frame + actual current frame.
            if step==0 and path is None: recorder.video_frame(initial_video)
            frames=sim.render(width,height); recorder.video_frame(video(frames))
            trial_progress('VLA rollout 已执行策略步=%s/%s 耗时=%.1fs',
                           step + 1, policy.config.task_horizon, time.monotonic() - started)
            if stop:
                reason=failures[0]
                status='simulation_failure' if reason in SIMULATION_FAILURES else 'failure'
                break
            if step%8==7 or step==policy.config.task_horizon-1:
                verdict=evidence.verdict()
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
        if path is not None:
            navigation.update(asdict(navigation_contacts))
        if navigation['status']=='running': navigation.update(status='unknown',reason=reason)
        mark_episode_phase('finalize')
        physics.close(); actions.close(); requests.close()
        termination = {'status': 'completed' if status in ('success', 'failure') else status, 'reason': reason}
        if status == 'timeout':
            verdict = evidence.verdict()
            if verdict['status'] == 'success':
                status, reason = 'success', verdict['reason']
        task_outcome = {'status': status if status in ('success', 'failure') else 'unknown', 'reason': reason}
        outcome={'status':status,'reason':reason,'verdict':verdict,'protocol':PROTOCOL,
            'execution_termination':termination, 'task_outcome':task_outcome,
            'exception_detail':exception_detail,
            'operation_backend':config.operation_backend,'continuous_simulation':True,'navigation':navigation,
            'gripper_mode':getattr(policy.config,'gripper_mode','embodiment_default'),'seed':seed,'elapsed_wall_s':time.monotonic()-started,
            'trajectory_metrics':contact_stats.metrics(task_success=status=='success' if status in ('success','failure') else None,
                simulation_valid=False if status=='simulation_failure' else True if sim.started else None),
            'contact_policy':CONTACT_POLICY,
            'attribution':{'category':'SimulationFailure' if status=='simulation_failure' else 'EpisodeTimeout' if status=='timeout' else 'PolicyFailure' if status=='failure' else 'Unknown',
                           'reason':reason,'collisions':collisions},
            'control':{'seed':seed,'policy':'pi05' if pi05 else 'MolmoBot-RBY1Multitask'}}
        recorder.finish(status, reason)
    return {**outcome, 'status': recorder.result['status'], 'station_id': station['station_id'],
        'rollout_id': recorder.attempt_id, 'attempt': str(recorder.path), 'executed_steps': recorder.steps,
        'recording_complete': recorder.result['recording_complete'],
        'observation_complete': recorder.result['observation_complete']}
