"""Continuous raw-scene navigation/manipulation through the existing 20D bridge."""
import copy
import gzip
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
import traceback
import mujoco
import numpy as np

from .episode_guard import isolated_episode, track_resource, mark_episode_phase, classify_episode_exception, episode_phase
from ..io import canonical, read_json, write_json
from ..scenes.geometry import body_pose, tcp_pose
from .action import InvalidAction
from .action_limits import sanitize_action
from .torso_command import bounded_torso_command, torso_feedback_margin, COMMAND_TOLERANCE_RAD, FEEDBACK_TOLERANCE_RAD, TORSO_SEVERE_FEEDBACK_RAD
from ..data.recorder import AttemptRecorder
from ..scenes.geometry import descendants
from ..scenes.sampling import place_frozen_station, robot_contacts
from .outcomes import attribute, evaluate_open, mobile_constraint_failure, evaluate_mobile_pick, torso_tracking_error
from .pick import PickMonitor, rotation_error
from .contacts import ContactStatistics, simulation_failure, SIMULATION_FAILURES, CONTACT_POLICY, contact_observations
from .simulation import Simulation


class OperationFailure(RuntimeError):
    def __init__(self,reason,diagnostic=None):
        super().__init__(reason); self.reason=reason; self.diagnostic=diagnostic


def clone_sim(sim, *, target=None):
    """New MjData, shared read-only model; never restore the running instance."""
    result=Simulation(sim.model,sim.robot.config,copy.deepcopy(sim.catalog),target=target)
    track_resource(result)
    mujoco.mj_setState(result.model,result.data,sim.state_vector(),sim.state_spec)
    result.robot.fixed_head=sim.robot.fixed_head.copy(); result._clock_remainder=sim._clock_remainder
    result.source=sim.source; result.restoration=None; result.spec=None
    mujoco.mj_forward(result.model,result.data)
    return result


from .execution_checks import EXECUTION_SEMANTICS, PickEvidenceComplete, checkpoint_pick_success, hold_pick, resolve_timeout, timed


class ContinuousContext:
    def __init__(self,sim,recorder,config,*,record_rgb=True,record_png=True):
        self.sim,self.recorder,self.config=sim,recorder,config
        self.phase='initialize'; self.started_at=time.monotonic()
        self.initial_sim_time=float(sim.data.time); self.timings={}; self.pick_initial=None; self.active_control=None
        self.record_rgb=record_rgb
        self.record_png=record_png
        from ..data.recorder import open_trace
        self.physics=track_resource(open_trace(recorder.path/'physics.jsonl.gz', recorder.collection.get('record_physics', False)))
        self.contact_stats=ContactStatistics()
        self.samples=[]; self.collisions=self.contact_stats.examples; self.plans=[]; self.replay=[]; self.monitor=None
        self.fingers=set(); self.target_bodies=descendants(sim.model,sim.target_id)
        self.last_frames=None; self.plan_count=0
        # Monotonic across retries: plan_count is intentionally reset per attempt so
        # its budget applies to that attempt, but evidence directories must stay unique.
        self.plan_serial=0
        self.analysis=None;self.initial_target_height=float(sim.data.xpos[sim.target_id,2])
        self.max_plans=config.max_operation_plans
        self.last_legal_torso_target=None

    def deadline(self):
        if time.monotonic()-self.started_at > self.config.attempt_timeout_s:
            raise TimeoutError('attempt_wall_clock_budget')

    def begin(self):
        if self.sim.started: return
        self.deadline()
        if self.record_rgb:
            with timed(self, 'rendering'):
                self.last_frames=self.sim.render(self.config.width,self.config.height)
            if self.sim.third_person_camera is not None:
                write_json(self.recorder.path/'third_person_initial.json',self.sim.third_person_camera.metadata())
            self.last_video_frames=self.video_frames(self.last_frames,float(self.sim.data.time))
            if self.record_png:
                with timed(self, 'recording'):
                    self.recorder.observation(self.last_frames,step=0,time_s=float(self.sim.data.time))
        self.sim.begin()

    def hold_torso_target(self):
        """Keep the last submitted target, not a physically overshooting qpos."""
        target = getattr(self, 'last_legal_torso_target', None)
        if target is None:
            target = bounded_torso_command(self.sim.robot.group('torso')[1], self.sim.robot.config.torso_limits)
        return target

    def tick(self,action,*,fixed_base=False):
        raw_action=np.asarray(action, dtype=float).copy()
        self.recorder.phase = self.phase
        action=raw_action.copy()
        action[19]=bounded_torso_command(action[19],self.sim.robot.config.torso_limits)
        if action[19] != raw_action[19]:
            # Append evidence before stepping; never rewrite qpos or loosen model limits.
            event={'phase':self.phase,'time_s':float(self.sim.data.time),
                   'input_h':float(raw_action[19]),'submitted_h':float(action[19]),
                   'command_tolerance_rad':COMMAND_TOLERANCE_RAD}
            with (self.recorder.path/'torso_command_adjustments.jsonl').open('ab') as stream:
                stream.write(canonical(event)+b'\n')
        action,adjustments=sanitize_action(self.sim.robot,action,fixed_base=fixed_base)
        for adjustment in adjustments:
            adjustment.update(phase=self.phase,time_s=float(self.sim.data.time))
            with (self.recorder.path/'command_limit_adjustments.jsonl').open('ab') as stream:
                stream.write(canonical(adjustment)+b'\n')
        self.deadline(); self.begin(); before=self.state(); failures=[]
        def check():
            health=simulation_failure(self.sim)
            if health:
                failures.append(health)
                self.physics.write(canonical({'time_s':float(self.sim.data.time),'issue':health})+b'\n')
                return {'valid':False,'issues':[{'severity':'error','code':health}]}
            mujoco.mj_forward(self.sim.model,self.sim.data)
            contacts=contact_observations(self.sim,target_bodies=self.target_bodies,fingers=self.fingers)
            self.contact_stats.update(contacts)
            row={'time_s':float(self.sim.data.time),'phase':self.phase,'collisions':contacts}
            # `record_only` protocol: a torso feedback excursion is telemetry, not a
            # stop condition. Only a gross violation (real loss of control) stops.
            h_actual=float(self.sim.robot.group('torso')[1])
            margin=torso_feedback_margin(h_actual,self.sim.robot.config.torso_limits)
            row.update(torso_measured_h=h_actual,torso_limit_excess_rad=margin,
                       torso_feedback_tolerance_rad=FEEDBACK_TOLERANCE_RAD)
            if margin > TORSO_SEVERE_FEEDBACK_RAD:
                failures.append('torso_feedback_limit')
            if self.monitor:
                self.monitor.phase=self.phase; self.monitor.command_h=float(action[19])
                facts=self.monitor.sample()
                facts.update(torso_tracking_error_rad=torso_tracking_error(facts),
                             torso_error_policy=self.config.torso_error_policy)
                row.update(facts); self.samples.append(facts)
                fail=mobile_constraint_failure(facts,self.monitor.initial)
                if fail: failures.append(fail)
                if hasattr(self,'open_joint'):
                    facts['joint_value']=float(self.sim.data.qpos[self.open_joint]); row['joint_value']=facts['joint_value']
            self.physics.write(canonical(row)+b'\n')
            return {'valid':not failures,'issues':[{'severity':'error','code':v} for v in failures]}
        with timed(self, 'simulation_and_physics_evidence'):
            command,stop=self.sim.step(action,fixed_base=fixed_base,substep_check=check,command_raw=raw_action)
        self.last_legal_torso_target=float(action[19])
        with timed(self, 'recording'):
            self.physics.flush(); after=self.state()
            self.recorder.record_step(raw_action=raw_action.tolist(),command=command,before=before,after=after,
                check=stop or {'valid':True,'issues':[]},observations={'phase':self.phase,
                         'index_step':self.recorder.steps+1,
                         'manifest':'observations.json' if self.record_png else 'videos.json'})
        if self.record_rgb:
            with timed(self, 'rendering'):
                frames=self.sim.render(self.config.width,self.config.height)
            with timed(self, 'recording'):
                if self.record_png:
                    self.recorder.observation(frames,step=self.recorder.steps,time_s=after['time_s'])
                if self.recorder.steps == 1: self.recorder.video_frame(self.last_video_frames,before['time_s'])
                self.recorder.video_frame(self.video_frames(frames,after['time_s']),after['time_s'])
        if failures: raise OperationFailure(failures[0])
        checkpoint_pick_success(self)

    def video_frames(self,frames,time_s):
        if self.analysis is None or 'third_person_camera' not in frames: return frames
        result=dict(frames)
        facts=self.samples[-1] if self.monitor is not None and self.samples else None
        result['third_person_camera']=self.analysis.frame(frames,phase=self.phase,time_s=time_s,
            base_xy=self.sim.robot.group('base')[:2],target_height=float(self.sim.data.xpos[self.sim.target_id,2]),
            initial_height=self.initial_target_height,facts=facts,
            side=self.monitor.side if self.monitor is not None else None,collisions=self.contact_stats.forbidden_contact_count)
        return result

    def yaw_gap(self, target, current):
        return float(target-current)

    def arm_tick(self,side,q,grip,h):
        a=self.sim.robot.neutral_action(); offset=3 if side=='left' else 11
        a[offset:offset+7]=np.asarray(q)-self.sim.robot.group(side+'_arm'); a[10 if side=='left' else 18]=grip; a[19]=h
        if self.monitor:
            idle='right' if side=='left' else 'left'; i=11 if side=='left' else 3
            a[i:i+7]=np.asarray(self.monitor.initial['idle_arm'])-self.sim.robot.group(idle+'_arm')
        if hasattr(self,'manip_base_target'):
            delta=self.manip_base_target-self.sim.robot.group('base')
            delta[2]=self.yaw_gap(self.manip_base_target[2],self.sim.robot.group('base')[2])
            limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
            yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a[:3]=delta/scale
        self.tick(a,fixed_base=False)

    def mobile_tick(self,planner,point,side,grip,h):
        """One synchronized base+arm waypoint increment; never writes qpos."""
        point=np.asarray(point)
        torso_active=getattr(planner,'torso_active',False)
        expected=['base_x','base_y','base_theta']+[f'{side}_arm_{i}' for i in range(7)]
        if torso_active: expected.append('torso_1')
        if tuple(planner.names) != tuple(expected) or point.shape != (len(expected),):
            raise ValueError('mobile planner joint ordering mismatch')
        target=planner.base_target_world(point)
        base_gap=target-self.sim.robot.group('base'); base_gap[2]=self.yaw_gap(target[2],self.sim.robot.group('base')[2])
        arm_gap=point[3:10]-self.sim.robot.group(side+'_arm')
        measured_h=float(self.sim.robot.group('torso')[1])
        h_gap=float(point[10])-measured_h if torso_active else 0.
        limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
        yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
        scale=max(1.,np.linalg.norm(base_gap[:2])/limit,abs(base_gap[2])/yaw_limit,
                  np.max(np.abs(arm_gap))/(self.sim.robot.config.max_arm_delta*.9),
                  abs(h_gap)/getattr(planner,'h_step_limit',.00125))
        a=self.sim.robot.neutral_action(); a[:3]=base_gap/scale
        offset=3 if side=='left' else 11; a[offset:offset+7]=arm_gap/scale
        a[10 if side=='left' else 18]=grip; a[19]=float(np.clip(measured_h+h_gap/scale,*self.sim.robot.config.torso_limits)) if torso_active else h
        if self.monitor:
            idle='right' if side=='left' else 'left'; i=11 if side=='left' else 3
            a[i:i+7]=np.asarray(self.monitor.initial['idle_arm'])-self.sim.robot.group(idle+'_arm')
        self.manip_base_target=target
        self.tick(a,fixed_base=False)
        return scale

    def state(self):
        return self.sim.observe_state()

    def plan(self,planner,goal):
        self.deadline()
        if self.plan_count >= self.max_plans: raise OperationFailure('planning_budget_no_solution')
        self.plan_count+=1
        with timed(self, 'motion_planning'):
            result=planner.plan(goal)
        self.deadline()
        self.plans.append({'phase':self.phase,'goal_world':goal.tolist(),**asdict(result)})
        write_json(self.recorder.path/'planning.json',self.plans)
        if result.status != 'success':
            diagnostic=dict(result.diagnostics)
            if 'IK' in str(diagnostic.get('status','')).upper(): diagnostic.update(planner.free_ik_diagnostic(goal))
            raise OperationFailure(diagnostic.get('failure_reason') or
                                   ('fov_constraint_failed' if result.status == 'fov_constraint_failed' else 'planning_no_solution'),diagnostic)
        return result

    def follow(self,planner,goal,side,grip,h):
        result=self.plan(planner,goal)
        self.follow_points(planner,result.positions,side,grip,h,goal)

    def follow_points(self,planner,positions,side,grip,h,goal=None):
        if not positions: raise OperationFailure('empty_trajectory')
        if not planner.mobile_base: raise ValueError('no-edit operation requires mobile-base cuRobo plan')
        for point in positions:
            for _ in range(120):
                if self.mobile_tick(planner,point,side,grip,h) <= 1.: break
            else: raise OperationFailure('arm_tracking_timeout')
        goal_q=np.asarray(positions[-1])
        for _ in range(100):
            base_gap=planner.base_target_world(goal_q)-self.sim.robot.group('base')
            base_gap[2]=self.yaw_gap(planner.base_target_world(goal_q)[2],self.sim.robot.group('base')[2])
            if (np.max(np.abs(self.sim.robot.group(side+'_arm')-goal_q[3:10])) < .01
                and np.linalg.norm(base_gap[:2]) < self.config.arrival_tolerance_m
                and abs(base_gap[2]) < self.config.arrival_tolerance_rad): break
            self.mobile_tick(planner,goal_q,side,grip,h)
        else: raise OperationFailure('mobile_base_arm_tracking_timeout')
        measured=tcp_pose(self.sim,side)
        if goal is not None and (np.linalg.norm(measured[:3,3]-goal[:3,3]) > .015 or rotation_error(measured[:3,:3],goal[:3,:3]) > .06):
            raise OperationFailure('tcp_tracking_timeout')

    def finish(self):
        self.physics.close()
        if self.plans:
            write_json(self.recorder.path / 'planning.json', self.plans)


def planner_options(config,assets_dir,seed):
    return SimpleNamespace(**{**asdict(config),'seed':seed,
                           'max_candidates':config.max_grasp_candidates,
                           'robot_planner_dir':str(Path(assets_dir)/'robots/rby1m/curobo_config')})


def arm_reach_bound(sim,side):
    """Triangle-inequality bound from the actual shoulder→TCP chain, not FK percentiles."""
    m=sim.model; shoulder=m.body('robot_0/link_'+side+'_arm_0').id
    site=m.site('robot_0/ee_site_'+side[0]).id; body=int(m.site_bodyid[site])
    bound=float(np.linalg.norm(m.site_pos[site]))
    while body != shoulder:
        if body == 0: raise ValueError('TCP not descended from shoulder')
        bound+=float(np.linalg.norm(m.body_pos[body]))
        # Non-origin hinge anchors can shift the body origin under rotation.
        for j in range(int(m.body_jntadr[body]),int(m.body_jntadr[body]+m.body_jntnum[body])):
            bound+=2*float(np.linalg.norm(m.jnt_pos[j]))
        body=int(m.body_parentid[body])
    return bound+.005


def manipulate(ctx, task, candidates, assets_dir, seed, forced=None):
    from .curobo_execution import manipulate_v2
    return manipulate_v2(ctx, task, candidates, assets_dir, seed, forced)


def navigate(ctx,path,goal):
    sim=ctx.sim; cfg=ctx.config; ctx.phase='navigation'; ctx.monitor=None; ctx.fingers=set()
    held={side:sim.robot.group(side+'_arm').copy() for side in ('left','right')}
    start_yaw=sim.robot.group('base')[2]; yaw_goal=goal['base'][2]; points=path['xy']; count=0
    # Same closed-loop torso command as arm_tick/mobile_tick: hold the last legal
    # target, re-derive it from feedback so gravity sag is corrected instead of
    # accumulating. A raw absolute command (or neutral_action's ctrl echo) is open
    # loop and lets the measured h drift below the lower bound.
    hold=getattr(ctx,'hold_torso_target',None)
    for index,xy in enumerate(points):
        fraction=index/max(1,len(points)-1)
        yaw=start_yaw+fraction*ctx.yaw_gap(yaw_goal,start_yaw)
        target=np.r_[xy,yaw]
        while True:
            current=sim.robot.group('base'); delta=target-current; delta[2]=ctx.yaw_gap(target[2],current[2])
            if np.linalg.norm(delta[:2]) <= cfg.arrival_tolerance_m and abs(delta[2]) <= cfg.arrival_tolerance_rad: break
            count+=1
            if count > cfg.max_navigation_steps: raise OperationFailure('navigation_tracking_timeout')
            limit=min(sim.robot.config.max_base_delta,cfg.navigation_speed_m_s/sim.robot.config.control_hz)
            yaw_limit=min(sim.robot.config.max_yaw_delta,cfg.navigation_yaw_speed_rad_s/sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a=sim.robot.neutral_action(); a[:3]=delta/scale
            a[3:10]=held['left']-sim.robot.group('left_arm'); a[11:18]=held['right']-sim.robot.group('right_arm')
            if hold is not None: a[19]=hold()
            ctx.tick(a)
    ctx.phase='arrival'
    for _ in range(10):
        a=sim.robot.neutral_action(); delta=np.asarray(goal['base'])-sim.robot.group('base')
        delta[2]=ctx.yaw_gap(goal['base'][2],sim.robot.group('base')[2])
        if np.linalg.norm(delta[:2]) > sim.robot.config.max_base_delta or abs(delta[2]) > sim.robot.config.max_yaw_delta:
            raise OperationFailure('arrival_tracking_error')
        a[:3]=delta
        if hold is not None: a[19]=hold()
        ctx.tick(a)
    actual=sim.robot.group('base')
    xy_error=float(np.linalg.norm(actual[:2]-np.asarray(goal['base'])[:2]))
    yaw_error=abs(ctx.yaw_gap(goal['base'][2],actual[2]))
    if xy_error > cfg.arrival_tolerance_m or yaw_error > cfg.arrival_tolerance_rad:
        raise OperationFailure('arrival_tracking_error')
    return {'status':'success','actual_base':actual.tolist(),'steps':count,
            'xy_error_m':xy_error,'yaw_error_rad':yaw_error,
            'arrival_tolerance_m':cfg.arrival_tolerance_m,'arrival_tolerance_rad':cfg.arrival_tolerance_rad}


@isolated_episode
def run_curobo_attempt(baseline,task,station,candidates,assets_dir,config,collection,root,attempt_id,
                    *,seed,path=None,goal=None,winning_control=None):
    sim=clone_sim(baseline,target=task['target_body']); place_frozen_station(sim,station)
    if config.third_person_enabled: sim.enable_third_person()
    # One live recorder; optional diagnostic streams share this rollout.
    packet={'execution_semantics_version':EXECUTION_SEMANTICS,'robot':asdict(sim.robot.config),'collection':asdict(collection),
            'task':task,'station':station,'protocol':asdict(config),'contact_policy':CONTACT_POLICY,'seed':seed,'path':path,'goal':goal,
            'scene':getattr(baseline, 'scene_id', None)}
    recorder=AttemptRecorder(root,packet,attempt_id=attempt_id,strategy='lastmile_rollout' if path else 'station_rollout')
    recorder.bind_simulation(sim, width=config.width, height=config.height)
    ctx=ContinuousContext(sim,recorder,config,record_rgb=True,record_png=collection.record_images)
    if config.third_person_enabled:
        from ..data.analysis_video import AnalysisVideo
        from ..scenes.tasks import robot_footprint
        footprint=robot_footprint(sim)
        ctx.analysis=AnalysisVideo(task,station,path=path,goal=goal,radius_m=config.radius_m,
                                   footprint_radius_m=footprint['radius_m'])
        write_json(recorder.path/'analysis_video.json',{'camera':'third_person_camera',
            'style':'observer + synchronized wrist inset + measured facts + planned/actual path',
            'station_footprint':footprint,'station_circle_inflation_m':0.,
            'dimensions':[1280,720],'playback_speed':1.,'raw_robot_cameras_unchanged':True,
            'third_person_png':'raw observer RGB','overlays':'actual simulation state; unmeasured contact shown as --'})
    write_json(recorder.path/'initial_state.json',ctx.state())
    mark_episode_phase('execution')
    status='infrastructure_error'; reason='unfinished'; control=None
    if task['operation']=='open': ctx.max_plans=config.max_open_plans
    navigation={'status':'not_requested'}; attribution=None; retries=[]
    execution_termination=None; task_outcome=None
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
            observed=locals().get('verdict')
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
                'exception_detail':locals().get('exception_detail'),
                'navigation':navigation,'retry_history':retries,'continuous_simulation':True,
                'operation_base_mode':config.operation_base_mode,
                'planner_backend':config.planner_backend,
                'manipulation_protocol':'mobile-pick-v2' if task['operation']=='pick' else 'mobile-open-v2',
                'torso_error_policy':config.torso_error_policy, 'contact_policy':CONTACT_POLICY,
                'trajectory_metrics':ctx.contact_stats.metrics(
                    task_success=task_outcome['status']=='success' if task_outcome['status'] in ('success','failure') else None,
                    simulation_valid=False if status=='simulation_failure' else True if sim.started else None)}
        finish_started=time.monotonic()
        recorder.finish(status,reason,{'scene_validity':{'status':'valid','scope':'robot initialization'},
                        'case_condition':{'status':'not_applicable','reason':'no_scene_edit'},
                        'task_completion':{'status':task_outcome['status'] if task_outcome['status'] in ('success','failure') else 'unknown','reason':task_outcome['reason']}},
                        extra={'execution_termination':execution_termination,'task_outcome':task_outcome,
                               'execution_semantics_version':EXECUTION_SEMANTICS,
                               'navigation':navigation,'phase':ctx.phase,
                               'operation_base_mode':config.operation_base_mode,
                               'contact_policy':CONTACT_POLICY, 'trajectory_metrics':result['trajectory_metrics']})
        diagnostics['recorder_finalize_wall_s']=time.monotonic()-finish_started
        diagnostics['wall_including_recorder_finalize_s']=time.monotonic()-ctx.started_at
        sim.close()
    result['status'] = recorder.result['status']
    return {**result, 'station_id': station['station_id'], 'attempt': str(recorder.path),
            'rollout_id': recorder.attempt_id, 'seed': seed, 'executed_steps': recorder.steps,
            'recording_complete': recorder.result['recording_complete']}
