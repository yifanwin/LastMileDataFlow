"""Continuous raw-scene navigation/manipulation through the existing 20D bridge."""
import copy
import gzip
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
import mujoco
import numpy as np

from ..io import canonical,read_json,write_json
from ..planning.curobo import NativePlanner,body_pose,tcp_pose
from ..recording.recorder import AttemptRecorder
from ..scenes.geometry import descendants
from ..stations.no_edit_sampling import place_frozen_station,robot_contacts
from ..validation.no_edit import attribute,evaluate_open,mobile_constraint_failure,evaluate_mobile_pick,torso_tracking_error
from ..validation.pick import PickMonitor,rotation_error
from .simulation import Simulation


class OperationFailure(RuntimeError):
    def __init__(self,reason,diagnostic=None):
        super().__init__(reason); self.reason=reason; self.diagnostic=diagnostic


def clone_sim(sim, *, target=None):
    """New MjData, shared read-only model; never restore the running instance."""
    result=Simulation(sim.model,sim.robot.config,copy.deepcopy(sim.catalog),target=target)
    mujoco.mj_setState(result.model,result.data,sim.state_vector(),sim.state_spec)
    result.robot.fixed_head=sim.robot.fixed_head.copy(); result._clock_remainder=sim._clock_remainder
    result.source=sim.source; result.restoration=None; result.model_hash=sim.model_hash
    result.frozen_provenance=getattr(sim,'frozen_provenance',None)
    if result.frozen_provenance is None: result.frozen_provenance=sim.source.provenance()
    mujoco.mj_forward(result.model,result.data)
    return result


class ContinuousContext:
    def __init__(self,sim,recorder,config,*,record_rgb=True,record_png=True):
        self.sim,self.recorder,self.config=sim,recorder,config
        self.phase='initialize'; self.started_at=time.monotonic()
        self.record_rgb=record_rgb
        self.record_png=record_png
        self.physics=gzip.open(recorder.path/'physics.jsonl.gz','wb')
        self.samples=[]; self.collisions=[]; self.plans=[]; self.replay=[]; self.monitor=None
        self.fingers=set(); self.target_bodies=descendants(sim.model,sim.target_id)
        self.last_frames=None; self.plan_count=0
        self.analysis=None;self.initial_target_height=float(sim.data.xpos[sim.target_id,2])
        self.max_plans=config.max_operation_plans

    def deadline(self):
        if time.monotonic()-self.started_at > self.config.attempt_timeout_s:
            raise TimeoutError('attempt_wall_clock_budget')

    def begin(self):
        if self.sim.started: return
        self.deadline()
        if self.record_rgb:
            self.last_frames=self.sim.render(self.config.width,self.config.height)
            if self.sim.third_person_camera is not None:
                write_json(self.recorder.path/'third_person_initial.json',self.sim.third_person_camera.metadata())
            self.last_video_frames=self.video_frames(self.last_frames,float(self.sim.data.time))
            if self.record_png:
                self.recorder.observation(self.last_frames,step=0,time_s=float(self.sim.data.time))
        self.replay.append((float(self.sim.data.time),self.sim.data.qpos.copy(),'initial')); self.sim.begin()

    def tick(self,action,*,fixed_base=False):
        self.deadline(); self.begin(); before=self.state(); failures=[]
        def check():
            mujoco.mj_forward(self.sim.model,self.sim.data)
            contacts=robot_contacts(self.sim,target_bodies=self.target_bodies,fingers=self.fingers)
            if contacts: self.collisions.extend(contacts); failures.append('robot_collision')
            if not all(np.isfinite(a).all() for a in (self.sim.data.qpos,self.sim.data.qvel,self.sim.data.qacc)):
                failures.append('nonfinite_physics')
            if any(w.number for w in self.sim.data.warning): failures.append('mujoco_warning')
            row={'time_s':float(self.sim.data.time),'phase':self.phase,'collisions':contacts}
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
        command,stop=self.sim.step(action,fixed_base=fixed_base,substep_check=check)
        self.physics.flush(); after=self.state()
        self.recorder.record_step(raw_action=np.asarray(action).tolist(),command=command,before=before,after=after,
            check=stop or {'valid':True,'issues':[]},observations={'phase':self.phase,
                     'index_step':self.recorder.steps+1,
                     'manifest':'observations.json' if self.record_png else 'videos.json'})
        self.replay.append((after['time_s'],self.sim.data.qpos.copy(),self.phase))
        if self.record_rgb:
            frames=self.sim.render(self.config.width,self.config.height)
            if self.record_png:
                self.recorder.observation(frames,step=self.recorder.steps,time_s=after['time_s'])
            if self.recorder.steps == 1: self.recorder.video_frame(self.last_video_frames,before['time_s'])
            self.recorder.video_frame(self.video_frames(frames,after['time_s']),after['time_s'])
        if failures: raise OperationFailure(failures[0])

    def video_frames(self,frames,time_s):
        if self.analysis is None or 'third_person_camera' not in frames: return frames
        result=dict(frames)
        facts=self.samples[-1] if self.monitor is not None and self.samples else None
        result['third_person_camera']=self.analysis.frame(frames,phase=self.phase,time_s=time_s,
            base_xy=self.sim.robot.group('base')[:2],target_height=float(self.sim.data.xpos[self.sim.target_id,2]),
            initial_height=self.initial_target_height,facts=facts,
            side=self.monitor.side if self.monitor is not None else None,collisions=len(self.collisions))
        return result

    def arm_tick(self,side,q,grip,h):
        a=self.sim.robot.neutral_action(); offset=3 if side=='left' else 11
        a[offset:offset+7]=np.asarray(q)-self.sim.robot.group(side+'_arm'); a[10 if side=='left' else 18]=grip; a[19]=h
        if self.monitor:
            idle='right' if side=='left' else 'left'; i=11 if side=='left' else 3
            a[i:i+7]=np.asarray(self.monitor.initial['idle_arm'])-self.sim.robot.group(idle+'_arm')
        if hasattr(self,'manip_base_target'):
            delta=self.manip_base_target-self.sim.robot.group('base')
            delta[2]=(delta[2]+np.pi)%(2*np.pi)-np.pi
            limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
            yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a[:3]=delta/scale
        self.tick(a,fixed_base=False)

    def mobile_tick(self,planner,point,side,grip,h):
        """One synchronized base+arm waypoint increment; never writes qpos."""
        point=np.asarray(point)
        if tuple(planner.names) != tuple(['base_x','base_y','base_theta']+[f'{side}_arm_{i}' for i in range(7)]):
            raise ValueError('mobile planner joint ordering mismatch')
        target=planner.base_target_world(point)
        base_gap=target-self.sim.robot.group('base'); base_gap[2]=(base_gap[2]+np.pi)%(2*np.pi)-np.pi
        arm_gap=point[3:]-self.sim.robot.group(side+'_arm')
        limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
        yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
        scale=max(1.,np.linalg.norm(base_gap[:2])/limit,abs(base_gap[2])/yaw_limit,
                  np.max(np.abs(arm_gap))/(self.sim.robot.config.max_arm_delta*.9))
        a=self.sim.robot.neutral_action(); a[:3]=base_gap/scale
        offset=3 if side=='left' else 11; a[offset:offset+7]=arm_gap/scale
        a[10 if side=='left' else 18]=grip; a[19]=h
        if self.monitor:
            idle='right' if side=='left' else 'left'; i=11 if side=='left' else 3
            a[i:i+7]=np.asarray(self.monitor.initial['idle_arm'])-self.sim.robot.group(idle+'_arm')
        self.manip_base_target=target
        self.tick(a,fixed_base=False)
        return scale

    def state(self):
        if self.record_png: return self.sim.observe_state()
        sim=self.sim; target=sim.target_id
        return {'time_s':float(sim.data.time),'robot':sim.robot.state(),
                'target':{'mjcf_body':sim.model.body(target).name,
                          'pose_world_xyz_wxyz':np.r_[sim.data.xpos[target],sim.data.xquat[target]].tolist()}}

    def plan(self,planner,goal):
        self.deadline()
        if self.plan_count >= self.max_plans: raise OperationFailure('planning_budget_no_solution')
        self.plan_count+=1; result=planner.plan(goal); self.deadline()
        self.plans.append({'phase':self.phase,'goal_world':goal.tolist(),**asdict(result)})
        write_json(self.recorder.path/'planning.json',self.plans)
        if result.status != 'success':
            diagnostic=dict(result.diagnostics)
            if 'IK' in str(diagnostic.get('status','')).upper(): diagnostic.update(planner.free_ik_diagnostic(goal))
            raise OperationFailure('planning_no_solution',diagnostic)
        return result

    def follow(self,planner,goal,side,grip,h):
        result=self.plan(planner,goal)
        if not planner.mobile_base: raise ValueError('no-edit operation requires mobile-base cuRobo plan')
        for point in result.positions:
            for _ in range(120):
                if self.mobile_tick(planner,point,side,grip,h) <= 1.: break
            else: raise OperationFailure('arm_tracking_timeout')
        goal_q=np.asarray(result.positions[-1])
        for _ in range(100):
            base_gap=planner.base_target_world(goal_q)-self.sim.robot.group('base')
            base_gap[2]=(base_gap[2]+np.pi)%(2*np.pi)-np.pi
            if (np.max(np.abs(self.sim.robot.group(side+'_arm')-goal_q[3:])) < .01
                and np.linalg.norm(base_gap[:2]) < self.config.arrival_tolerance_m
                and abs(base_gap[2]) < self.config.arrival_tolerance_rad): break
            self.mobile_tick(planner,goal_q,side,grip,h)
        else: raise OperationFailure('mobile_base_arm_tracking_timeout')
        measured=tcp_pose(self.sim,side)
        if np.linalg.norm(measured[:3,3]-goal[:3,3]) > .015 or rotation_error(measured[:3,:3],goal[:3,:3]) > .06:
            raise OperationFailure('tcp_tracking_timeout')

    def finish(self):
        self.physics.close(); write_json(self.recorder.path/'planning.json',self.plans)
        if self.sim.third_person_camera is not None:
            write_json(self.recorder.path/'third_person_final.json',self.sim.third_person_camera.metadata())
        if self.recorder.steps:
            np.savez_compressed(self.recorder.path/'replay.npz',times=np.array([r[0] for r in self.replay]),
                                qpos=np.array([r[1] for r in self.replay]),phases=np.array([r[2] for r in self.replay]))
            self.sim.save_snapshot(self.recorder.path/'final_snapshot.npz')
            write_json(self.recorder.path/'final_state.json',self.state())


def planner_options(config,assets_dir,seed):
    return SimpleNamespace(**{**asdict(config),'seed':seed,
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


def choose_control(ctx,task,candidates,assets_dir,seed,forced=None):
    """Dry candidate plans in a NEW diagnostic sim; the actual rollout is not reset."""
    dry=clone_sim(ctx.sim,target=task['target_body']); cfg=ctx.config
    rng=np.random.default_rng(seed); options=planner_options(cfg,assets_dir,seed)
    base=ctx.sim.robot.group('base'); scored=[]
    for candidate in candidates:
        goal=body_pose(ctx.sim,ctx.sim.model.body(candidate['body']).id)@candidate['pose_local']
        vector=goal[:3,3]-np.r_[base[:2],goal[2,3]]
        scored.append((float(np.dot(goal[:3,2],vector)/max(np.linalg.norm(vector),1e-9)),candidate))
    scored.sort(key=lambda x:-x[0]); pool=[c for _,c in scored[:max(cfg.max_grasp_candidates*8,16)]]
    if forced:
        pool=[c for c in candidates if c['row']==forced['grasp_row'] and c['source']==forced['grasp_source']]
        if not pool: dry.close(); raise ValueError('missing winning grasp')
    elif pool:
        pool=[pool[i] for i in rng.permutation(len(pool))[:cfg.max_grasp_candidates]]
    initial=copy.deepcopy(ctx.sim.robot.config.initial); state=ctx.sim.robot.state()
    for key in ('base','head','left_arm','right_arm'): initial[key]=state[key]
    initial['torso']=[float(state['torso'][1])]
    best_reason='planning_no_solution'; diagnostic=None
    path=ctx.recorder.path/f'dry_plans-{len(ctx.plans)}'; path.mkdir()
    try:
        for index,candidate in enumerate(pool):
            sides=[forced['arm']] if forced else (['left','right'] if index%2 == 0 else ['right','left'])
            h=forced['torso_h'] if forced else cfg.torso_heights[index%len(cfg.torso_heights)]
            if not forced and task['anchor_world'][2] < .8: h=max(cfg.torso_heights)
            initial['torso']=[h]; dry.robot.initialize(initial)
            goal=body_pose(dry,dry.model.body(candidate['body']).id)@candidate['pose_local']
            pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08
            # Fixed-shoulder reach rejection is invalid when x/y/yaw are active.
            contacts=robot_contacts(dry)
            if contacts:
                best_reason='robot_collision'; diagnostic={'dry_collisions':contacts}; continue
            for side in sides:
                ctx.deadline(); directory=path/f'{index}-{side}'; directory.mkdir()
                planner=NativePlanner(dry,options,side,directory,mobile_base=True,
                                      workspace=(task['anchor_world'],cfg.radius_m))
                try:
                    ctx.phase='dry_pregrasp'; ctx.plan(planner,pre)
                    return {'arm':side,'torso_h':h,'grasp_row':candidate['row'],
                            'grasp_source':candidate['source'],'candidate':candidate,'seed':seed}
                except OperationFailure as exc:
                    best_reason,diagnostic=exc.reason,exc.diagnostic
                    if ctx.plan_count >= ctx.max_plans-3: raise
                finally: del planner
        raise OperationFailure(best_reason,diagnostic)
    finally: dry.close()


def manipulate(ctx,task,candidates,assets_dir,seed,forced=None):
    control=choose_control(ctx,task,candidates,assets_dir,seed,forced)
    side,h=control['arm'],control['torso_h']; sim=ctx.sim
    ctx.monitor=PickMonitor(sim,side); ctx.fingers=ctx.monitor.fingers; ctx.samples=[]
    ctx.monitor.initial.update(workspace_center_xy=task['anchor_world'][:2],workspace_radius_m=ctx.config.radius_m)
    ctx.manip_base_target=sim.robot.group('base').copy()
    if task['operation']=='open':
        j=sim.model.joint(task['joint_name']).id; ctx.open_joint=int(sim.model.jnt_qposadr[j])
    initial=copy.deepcopy(ctx.monitor.initial)
    ctx.begin(); ctx.phase='torso_adjust'; q=sim.robot.group(side+'_arm').copy(); h0=float(sim.robot.group('torso')[1])
    for value in np.linspace(h0,h,max(2,int(abs(h-h0)/.00125)+1))[1:]: ctx.arm_tick(side,q,-.05,float(value))
    for _ in range(20): ctx.arm_tick(side,q,-.05,h)
    directory=ctx.recorder.path/f'measured_planner-{len(ctx.plans)}'; directory.mkdir()
    planner=NativePlanner(sim,planner_options(ctx.config,assets_dir,seed),side,directory,mobile_base=True,
                          workspace=(task['anchor_world'],ctx.config.radius_m))
    try:
        c=control['candidate']; goal=body_pose(sim,sim.model.body(c['body']).id)@c['pose_local']
        pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08
        ctx.phase='pregrasp'; ctx.follow(planner,pre,side,-.05,h)
        goal[:3,3]+=goal[:3,2]*ctx.config.approach_offset_m
        ctx.phase='approach'; ctx.follow(planner,goal,side,-.05,h)
        ctx.phase='close'; q=sim.robot.group(side+'_arm').copy()
        for _ in range(20): ctx.arm_tick(side,q,0.,h)
        if task['operation']=='pick':
            ctx.phase='lift'; lift=tcp_pose(sim,side).copy(); lift[2,3]+=.06; ctx.follow(planner,lift,side,0.,h)
            ctx.phase='lift_attached'; write_json(ctx.recorder.path/'attached_object.json',planner.attach_target_bbox())
            lift=tcp_pose(sim,side).copy(); lift[2,3]+=.04; ctx.follow(planner,lift,side,0.,h)
            ctx.phase='hold'; q=sim.robot.group(side+'_arm').copy()
            for _ in range(50): ctx.arm_tick(side,q,0.,h)
            verdict=evaluate_mobile_pick(ctx.samples,initial)
        else:
            if not ctx.samples or len(ctx.samples[-1]['finger_forces_n']) != 2: raise OperationFailure('open_no_grip')
            ctx.phase='open'; j=sim.model.joint(task['joint_name']).id
            step=ctx.config.open_waypoint_step_m if task['joint_kind']=='slide' else ctx.config.open_waypoint_step_rad
            budget=int(abs(task['joint_goal']-task['joint_initial'])/step)*3+12
            direction=np.sign(task['joint_goal']-task['joint_initial'])
            for index in range(budget):
                value=float(sim.data.qpos[ctx.open_joint]); remaining=direction*(task['joint_goal']-value)
                if remaining <= 1e-3: break
                delta=direction*min(step,remaining+.002); tcp=tcp_pose(sim,side).copy(); axis=sim.data.xaxis[j].copy()
                if task['joint_kind']=='slide': tcp[:3,3]+=axis*delta
                else:
                    skew=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
                    rotation=np.eye(3)+np.sin(delta)*skew+(1-np.cos(delta))*(skew@skew)
                    origin=sim.data.xanchor[j]; tcp[:3,3]=origin+rotation@(tcp[:3,3]-origin); tcp[:3,:3]=rotation@tcp[:3,:3]
                world_path=directory/f'world-{index}'; world_path.mkdir(); planner.refresh_world(world_path)
                ctx.follow(planner,tcp,side,0.,h)
                if len(ctx.samples[-1]['finger_forces_n']) != 2: raise OperationFailure('open_contact_lost')
            ctx.phase='open_hold'; q=sim.robot.group(side+'_arm').copy()
            for _ in range(int(np.ceil((ctx.config.open_hold_s+.1)*sim.robot.config.control_hz))): ctx.arm_tick(side,q,0.,h)
            verdict=evaluate_open(task,ctx.samples,ctx.config.open_hold_s)
        write_json(ctx.recorder.path/f'verdict-{len(ctx.plans)}.json',verdict)
        return verdict,{k:v for k,v in control.items() if k != 'candidate'}
    finally: del planner


def navigate(ctx,path,goal):
    sim=ctx.sim; cfg=ctx.config; ctx.phase='navigation'; ctx.monitor=None; ctx.fingers=set()
    held={side:sim.robot.group(side+'_arm').copy() for side in ('left','right')}
    start_yaw=sim.robot.group('base')[2]; yaw_goal=goal['base'][2]; points=path['xy']; count=0
    for index,xy in enumerate(points):
        fraction=index/max(1,len(points)-1)
        yaw=start_yaw+fraction*((yaw_goal-start_yaw+np.pi)%(2*np.pi)-np.pi)
        target=np.r_[xy,float((yaw+np.pi)%(2*np.pi)-np.pi)]
        while True:
            current=sim.robot.group('base'); delta=target-current; delta[2]=(delta[2]+np.pi)%(2*np.pi)-np.pi
            if np.linalg.norm(delta[:2]) <= cfg.arrival_tolerance_m and abs(delta[2]) <= cfg.arrival_tolerance_rad: break
            count+=1
            if count > cfg.max_navigation_steps: raise OperationFailure('navigation_tracking_timeout')
            limit=min(sim.robot.config.max_base_delta,cfg.navigation_speed_m_s/sim.robot.config.control_hz)
            yaw_limit=min(sim.robot.config.max_yaw_delta,cfg.navigation_yaw_speed_rad_s/sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a=sim.robot.neutral_action(); a[:3]=delta/scale
            a[3:10]=held['left']-sim.robot.group('left_arm'); a[11:18]=held['right']-sim.robot.group('right_arm')
            ctx.tick(a)
    ctx.phase='arrival'
    for _ in range(10):
        a=sim.robot.neutral_action(); delta=np.asarray(goal['base'])-sim.robot.group('base')
        delta[2]=(delta[2]+np.pi)%(2*np.pi)-np.pi
        if np.linalg.norm(delta[:2]) > sim.robot.config.max_base_delta or abs(delta[2]) > sim.robot.config.max_yaw_delta:
            raise OperationFailure('arrival_tracking_error')
        a[:3]=delta; ctx.tick(a)
    return {'status':'success','actual_base':sim.robot.group('base').tolist(),'steps':count}


def run_raw_attempt(baseline,task,station,candidates,assets_dir,config,collection,root,attempt_id,
                    *,seed,path=None,goal=None,winning_control=None):
    sim=clone_sim(baseline,target=task['target_body']); place_frozen_station(sim,station)
    if config.third_person_enabled: sim.enable_third_person()
    # Keep actual three-camera video for ALL executed trials: S0 is selected only
    # afterwards. Avoid duplicate PNG streams except on continuous demonstrations.
    packet={'schema_version':'no-edit-v1','robot':asdict(sim.robot.config),'collection':asdict(collection),
            'task':task,'station':station,'protocol':asdict(config),'seed':seed,'path':path,'goal':goal,
            'scene_version':read_json(root/'scene/version.json')['version_id']}
    recorder=AttemptRecorder(root,packet,attempt_id=attempt_id,strategy='no_edit_continuous' if path else 'no_edit_station')
    write_json(recorder.path/'source.json',{'frozen_scene':str(root/'scene'),'source':sim.source.provenance(),
               'initialization_only':station['base'],'no_scene_edits':True})
    ctx=ContinuousContext(sim,recorder,config,record_rgb=True,record_png=bool(path))
    if config.third_person_enabled:
        from ..recording.analysis_video import AnalysisVideo
        ctx.analysis=AnalysisVideo(task,station,path=path,goal=goal,radius_m=config.radius_m)
        write_json(recorder.path/'analysis_video.json',{'camera':'third_person_camera',
            'style':'observer + synchronized wrist inset + measured facts + planned/actual path',
            'dimensions':[1280,720],'playback_speed':1.,'raw_robot_cameras_unchanged':True,
            'third_person_png':'raw observer RGB','overlays':'actual simulation state; unmeasured contact shown as --'})
    write_json(recorder.path/'initial_state.json',ctx.state())
    status='infrastructure_error'; reason='unfinished'; control=None
    if task['operation']=='open': ctx.max_plans=config.max_open_plans
    navigation={'status':'not_requested'}; attribution=None; retries=[]
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
                status='planning_no_solution' if not recorder.steps else 'failure'; reason=exc.reason
                retries.append({'retry':retry,'reason':reason,'attribution':attribution})
                if retry >= (config.operation_retries if path else 0) or ctx.collisions: break
                initial=read_json(recorder.path/'initial_state.json')['target']['pose_world_xyz_wxyz']
                if np.linalg.norm(sim.data.xpos[sim.target_id]-np.asarray(initial[:3])) > .01: break
                ctx.monitor=None; ctx.phase='retry_release'
                for _ in range(10):
                    a=sim.robot.neutral_action(); a[10]=a[18]=-.05; ctx.tick(a,fixed_base=False)
                ctx.plan_count=0
        if status != 'success' and attribution is None:
            attribution=attribute(reason,samples=ctx.samples,collisions=ctx.collisions)
    except OperationFailure as exc:
        status='failure' if recorder.steps else 'planning_no_solution'; reason=exc.reason
        if ctx.phase in ('navigation','arrival'): navigation={'status':'failure','reason':reason}
        attribution=attribute(reason,samples=ctx.samples,collisions=ctx.collisions,diagnostic=exc.diagnostic)
    except TimeoutError as exc:
        status='incomplete'; reason=str(exc)
    except Exception as exc:
        status='infrastructure_error'; reason=type(exc).__name__+':'+str(exc)
        recorder.event('exception',phase=ctx.phase,error_type=type(exc).__name__,message=str(exc))
    finally:
        ctx.finish()
        result={'status':status,'reason':reason,'attribution':attribution,'control':control,
                'navigation':navigation,'retry_history':retries,'continuous_simulation':True,
                'operation_base_mode':config.operation_base_mode,
                'manipulation_protocol':'mobile-pick-v2' if task['operation']=='pick' else 'mobile-open-v2',
                'torso_error_policy':config.torso_error_policy}
        write_json(recorder.path/'outcome.json',result)
        recorder.finish(status,reason,{'scene_validity':{'status':'valid','scope':'robot initialization'},
                        'case_condition':{'status':'not_applicable','reason':'no_scene_edit'},
                        'task_completion':{'status':status if status in ('success','failure') else 'unknown','reason':reason}},
                        extra={'navigation':navigation,'phase':ctx.phase,'no_edit':True,
                               'operation_base_mode':config.operation_base_mode})
        sim.close()
    finalized=read_json(recorder.path/'result.json')
    if finalized['status']=='infrastructure_error': result['status']='infrastructure_error'
    media=read_json(recorder.path/'videos.json')
    recorded=bool(media['cameras']) and all((recorder.path/v['path']).is_file()
                    and v['frame_count']>=2 for v in media['cameras'].values())
    return {**result,'station_id':station['station_id'],'attempt':str(recorder.path),
            'seed':seed,'executed_steps':recorder.steps,'videos':str(recorder.path/'videos.json'),
            'video_status':'not_executed' if not recorder.steps else 'recorded' if recorded else 'disabled' if not collection.record_video else 'recording_error',
            'third_person_video':str(recorder.path/'videos/third_person_camera.mp4') if 'third_person_camera' in media['cameras'] else None,
            'operation_base_mode':config.operation_base_mode}
