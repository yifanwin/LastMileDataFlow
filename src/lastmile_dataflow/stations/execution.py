"""A single attempt, no reset after begin, bounded dynamic torso + measured-state plans."""
from dataclasses import asdict
from pathlib import Path
import time
import numpy as np
import mujoco
from ..io import write_json, canonical, read_json
from ..runtime.simulation import Simulation
from ..robots.action import InvalidAction
from ..recording.recorder import AttemptRecorder
from ..planning.curobo import NativePlanner,load_grasps,body_pose,tcp_pose
from ..validation.pick import PickMonitor,PROTOCOL,constraint_failure,evaluate_pick,rotation_error
from .sampling import place_base,filter_station


class ExecutionStop(RuntimeError): pass
class BudgetStop(RuntimeError): pass


class Budget:
    def __init__(self,config): self.config=config; self.started=time.monotonic(); self.plans=0; self.executions=0
    def check(self):
        if time.monotonic()-self.started>self.config.timeout_s: raise BudgetStop('run_wall_clock_budget')
    def plan(self):
        self.check()
        if self.plans>=self.config.max_plans: raise BudgetStop('planning_budget')
        self.plans+=1
    def execute(self):
        self.check()
        if self.executions>=self.config.max_executions: raise BudgetStop('execution_budget')
        self.executions+=1


def prepare(snapshot,robot,station,target):
    sim=Simulation.from_snapshot(snapshot,robot,target=target)
    try: placement=place_base(sim,station['base'])
    except BaseException: sim.close(); raise
    return sim,placement


def run_station_attempt(snapshot,robot,config,collection,station,side,h,gid,root,attempt_id,budget,planner_factory=NativePlanner):
    cfg={'schema_version':'3.0','robot':asdict(robot),'task':{'task_id':config.task_id,'target':config.target,'operation':'pick','case_type':'case1','fixed_base':True},
         'collection':asdict(collection),'station_protocol':asdict(config),'station':station,'side':side,'h':h,'grasp_row':gid,'pick_protocol':PROTOCOL}
    recorder=AttemptRecorder(root,cfg,attempt_id=attempt_id,strategy='fixed_base_curobo_v3',parent=str(Path(snapshot).resolve()))
    sim=None; monitor=None; phase='initialize'; samples=[]; replay=[]; plans=[]
    status='infrastructure_error'; reason='not_completed'; planning='not_tested'; execution='not_executed'; geometry='not_tested'
    results={'scene_validity':{'status':'unknown'},'case_condition':{'status':'unknown','reason':'requires_station_comparison'},'task_completion':{'status':'unknown'}}
    initial=None; attempt_start=time.monotonic()
    physics=(recorder.path/'physics.jsonl').open('xb')
    def deadline():
        budget.check()
        if time.monotonic()-attempt_start>config.attempt_timeout_s: raise BudgetStop('attempt_wall_clock_budget')
    def plan_goal(planner,goal):
        nonlocal planning
        budget.plan(); deadline(); result=planner.plan(goal)
        plans.append({'phase':phase,'goal_world':goal.tolist(),**asdict(result)})
        write_json(recorder.path/'planning.json',plans)
        planning=result.status
        return result
    def tick(q,gripper,torso):
        nonlocal phase
        deadline(); a=sim.robot.neutral_action(); offset=3 if side=='left' else 11
        a[offset:offset+7]=np.asarray(q)-sim.robot.group(side+'_arm')
        idle='right' if side=='left' else 'left'; idle_offset=11 if side=='left' else 3
        a[idle_offset:idle_offset+7]=np.asarray(initial['idle_arm'])-sim.robot.group(idle+'_arm')
        a[10 if side=='left' else 18]=gripper; a[19]=torso
        monitor.phase=phase; monitor.command_h=torso
        before=sim.observe_state(); subfailure=[]
        def check():
            mujoco.mj_forward(sim.model,sim.data)
            row=monitor.sample(); samples.append(row); physics.write(canonical(row)+b'\n')
            fail=constraint_failure(row,initial)
            if fail: subfailure.append(fail)
            return {'valid':not fail,'issues':[] if not fail else [{'severity':'error','code':fail}]}
        command,stop=sim.step(a,fixed_base=True,substep_check=check)
        physics.flush(); after=sim.observe_state()
        recorder.record_step(raw_action=a.tolist(),command=command,before=before,after=after,check=stop or {'valid':True,'issues':[]},observations={'phase':phase,'physics':'physics.jsonl'})
        replay.append((after['time_s'],sim.data.qpos.copy(),phase))
        if subfailure: raise ExecutionStop(subfailure[0])
    def follow(planner,goal,grip):
        result=plan_goal(planner,goal)
        if result.status!='success': raise ExecutionStop('planning_no_solution_after_execution')
        qlast=np.array(result.positions[-1])
        for point in result.positions:
            point=np.array(point)
            # Bound every commanded increment, do not clip a trajectory or teleport.
            while np.max(np.abs(point-sim.robot.group(side+'_arm')))>robot.max_arm_delta:
                now=sim.robot.group(side+'_arm'); gap=point-now
                tick(now+gap*(robot.max_arm_delta*.95/np.max(np.abs(gap))),grip,h)
            tick(point,grip,h)
        for _ in range(100):
            if np.max(np.abs(sim.robot.group(side+'_arm')-qlast))<.01: break
            tick(qlast,grip,h)
        measured=tcp_pose(sim,side)
        if np.linalg.norm(measured[:3,3]-goal[:3,3])>.01 or rotation_error(goal[:3,:3],measured[:3,:3])>.05: raise ExecutionStop('tcp_tracking_timeout')
    try:
        sim,placement=prepare(snapshot,robot,station,config.target)
        version=read_json(Path(snapshot)/'version.json'); grasps=load_grasps(config,sim)
        write_json(recorder.path/'source.json',{'snapshot':str(Path(snapshot).resolve()),'scene_version':version['version_id'],'grasp_sha256':config.grasp_sha256,'target':config.target,'snapshot_checksums':read_json(Path(snapshot)/'checksums.json')})
        check=filter_station(sim,collection); geometry=check['status']
        write_json(recorder.path/'initialization.json',{'placement':placement,'filter':check})
        results['scene_validity']={'status':'valid' if geometry=='valid' else 'invalid','scope':'independent_station_initialization'}
        if geometry!='valid':
            status='geometry_filtered'; reason='initial_collision_or_floor'
        else:
            # Diagnostic h is ONLY for dry planning; this simulation never executes.
            for n,q in zip(sim.robot.groups['torso'],[0,h,-2*h,h,0,0]): sim.data.qpos[sim.robot.addresses[n]]=q
            sim.robot.hold(); mujoco.mj_forward(sim.model,sim.data)
            phase='dry_pregrasp'
            dry=recorder.path/'dry_planner'; dry.mkdir()
            planner=planner_factory(sim,config,side,dry)
            goal=body_pose(sim,sim.target_id)@grasps[gid]; pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08
            pre_result=plan_goal(planner,pre)
            del planner
            sim.close(); sim=None
            if pre_result.status!='success':
                status='planning_no_solution'; reason='finite_budget_dry_pregrasp'
            else:
                budget.execute()
                sim,placement=prepare(snapshot,robot,station,config.target)
                monitor=PickMonitor(sim,side); initial=monitor.initial
                write_json(recorder.path/'pick_initial.json',initial)
                write_json(recorder.path/'initial_state.json',sim.observe_state())
                sim.save_snapshot(recorder.path/'station_initial.npz')
                replay.append((float(sim.data.time),sim.data.qpos.copy(),'initial'))
                sim.begin(); execution='failure'; phase='torso_adjust'
                q0=sim.robot.group(side+'_arm').copy(); h0=float(sim.robot.group('torso')[1])
                for value in np.linspace(h0,h,max(2,int(abs(h-h0)/.00125)+1))[1:]: tick(q0,-.05,float(value))
                for _ in range(20): tick(q0,-.05,h)
                phase='pregrasp'
                realdir=recorder.path/'measured_planner'; realdir.mkdir()
                planner=planner_factory(sim,config,side,realdir)
                goal=body_pose(sim,sim.target_id)@grasps[gid]; pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08
                follow(planner,pre,-.05)
                phase='approach'; goal[:3,3]+=goal[:3,2]*config.approach_offset_m
                follow(planner,goal,-.05)
                phase='close'
                q=sim.robot.group(side+'_arm').copy()
                for _ in range(20): tick(q,0.,h)
                phase='lift'; lift=tcp_pose(sim,side).copy(); lift[2,3]+=.10
                follow(planner,lift,0.)
                phase='hold'; q=sim.robot.group(side+'_arm').copy()
                for _ in range(50): tick(q,0.,h)
                verdict=evaluate_pick(samples,initial); write_json(recorder.path/'pick_verdict.json',verdict)
                execution=verdict['status']; status='executed_'+execution; reason=verdict['reason']
                results['task_completion']={'status':execution,'reason':reason}
                del planner
    except InvalidAction as exc:
        if phase=='initialize' and not recorder.steps:
            geometry='geometry_filtered'; status='geometry_filtered'; reason='initial_joint_limit'
            results['scene_validity']={'status':'invalid','reason':reason}
            write_json(recorder.path/'initialization.json',{'status':'geometry_filtered','reason':reason,'diagnostic':str(exc)})
        else:
            status='infrastructure_error'; reason='invalid_control'; execution='incomplete' if recorder.steps else 'not_executed'
            recorder.event('invalid_control',phase=phase,message=str(exc))
    except BudgetStop as exc:
        status='budget_exhausted'; reason=str(exc); execution='incomplete' if recorder.steps else 'not_executed'
    except ExecutionStop as exc:
        status='executed_failure'; reason=str(exc); execution='failure'
        results['task_completion']={'status':'failure','reason':reason}
        if monitor: write_json(recorder.path/'pick_verdict.json',evaluate_pick(samples,initial))
    except Exception as exc:
        status='infrastructure_error'; reason=f'{phase}:{type(exc).__name__}'
        execution='infrastructure_error' if recorder.steps else 'not_executed'
        planning='infrastructure_error' if phase in ('dry_pregrasp','pregrasp','approach','lift') else planning
        recorder.event('exception',phase=phase,error_type=type(exc).__name__,message=str(exc))
    finally:
        physics.close()
        if sim is not None:
            if sim.started:
                sim.save_snapshot(recorder.path/'final_snapshot.npz')
                write_json(recorder.path/'final_state.json',sim.observe_state())
            sim.close()
        write_json(recorder.path/'planning.json',plans)
        if replay and recorder.steps:
            np.savez_compressed(recorder.path/'replay.npz',times=np.array([r[0] for r in replay]),qpos=np.array([r[1] for r in replay]),phases=np.array([r[2] for r in replay]))
        write_json(recorder.path/'layers.json',{'geometry':geometry,'planning':planning,'execution':execution,
                   'first_failure_phase':phase if execution not in ('success','not_executed') else None,
                   'grasp_row':gid,'arm':side,'torso_h':h,'station_id':station['station_id'],
                   'finite_budget_not_impossibility':True})
        if recorder.steps and config.render_video:
            try:
                from ..exporting.station_report import render_attempt
                render_attempt(recorder,snapshot,robot,config,status,reason)
            except Exception as exc:
                recorder.event('render_error',error_type=type(exc).__name__,message=str(exc))
                status='infrastructure_error'; reason='render_or_encode_failed'
                results['task_completion']={'status':'unknown','reason':'recording_incomplete'}
        elif not recorder.steps:
            # Planning-only failures have genuinely empty action/video evidence.
            results['task_completion']={'status':'unknown','reason':'no_physical_execution'}
        if status=='infrastructure_error' and recorder.steps:
            write_json(recorder.path/'layers.json',{'geometry':geometry,'planning':planning,'execution':'infrastructure_error',
                       'physical_outcome_before_recording':execution,'first_failure_phase':phase,
                       'grasp_row':gid,'arm':side,'torso_h':h,'station_id':station['station_id'],
                       'finite_budget_not_impossibility':True})
        recorder.finish(status,reason,results,extra={'phase':phase,'wall_time_s':time.monotonic()-attempt_start,'schema':'3.0'})
    return recorder.path
