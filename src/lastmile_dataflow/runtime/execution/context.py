"""Continuous control context and shared execution helpers."""
import copy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
import mujoco
import numpy as np
from .lifecycle import track_resource
from ...utils.io import canonical, write_json
from ...scenes.geometry import tcp_pose
from ..control.action_limits import sanitize_action
from ..control.torso_command import bounded_torso_command, torso_feedback_margin, COMMAND_TOLERANCE_RAD, FEEDBACK_TOLERANCE_RAD, TORSO_SEVERE_FEEDBACK_RAD
from ...scenes.geometry import descendants
from ..outcomes.evaluation import mobile_constraint_failure, torso_tracking_error
from ..outcomes.pick import rotation_error
from ..outcomes.contacts import ContactStatistics, simulation_failure, contact_observations
from ..simulation import Simulation
from .checks import checkpoint_pick_success, timed

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


class ContinuousContext:
    def __init__(self, sim, recorder, config):
        self.sim, self.recorder, self.config = sim, recorder, config
        self.phase = 'initialize'
        self.started_at = time.monotonic()
        self.initial_sim_time = float(sim.data.time)
        self.timings = {}
        self.pick_initial = None
        self.active_control = None
        self.record_video = recorder.collection['record_video']
        from ...data.recorder import open_trace

        self.physics = track_resource(
            open_trace(recorder.path / 'physics.jsonl.gz', recorder.collection['record_physics'])
        )
        self.contact_stats = ContactStatistics()
        self.samples = []
        self.collisions = self.contact_stats.examples
        self.plans = []
        self.monitor = None
        self.fingers = set()
        self.target_bodies = descendants(sim.model, sim.target_id)
        self.plan_count = 0
        # Monotonic across retries: plan_count is intentionally reset per attempt so
        # its budget applies to that attempt, but evidence directories must stay unique.
        self.plan_serial = 0
        self.analysis = None
        self.initial_target_height = float(sim.data.xpos[sim.target_id, 2])
        self.max_plans = config.max_operation_plans
        self.last_legal_torso_target = None
        self.open_joint = None
        self.manip_base_target = None

    def deadline(self):
        if time.monotonic() - self.started_at > self.config.attempt_timeout_s:
            raise TimeoutError('attempt_wall_clock_budget')

    def begin(self):
        if self.sim.started: return
        self.deadline()
        self.sim.begin()
        if self.record_video:
            with timed(self, 'rendering'):
                frames=self.sim.render(self.config.width,self.config.height)
            if self.sim.third_person_camera is not None:
                write_json(self.recorder.path/'third_person_initial.json',self.sim.third_person_camera.metadata())
            self.last_video_frames=self.video_frames(frames,float(self.sim.data.time))

    def hold_torso_target(self):
        """Keep the last submitted target, not a physically overshooting qpos."""
        target = self.last_legal_torso_target
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
        self.deadline()
        self.begin()
        failures=[]
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
                if self.open_joint is not None:
                    facts['joint_value']=float(self.sim.data.qpos[self.open_joint]); row['joint_value']=facts['joint_value']
            self.physics.write(canonical(row)+b'\n')
            return {'valid':not failures,'issues':[{'severity':'error','code':v} for v in failures]}
        with timed(self, 'simulation_and_physics_evidence'):
            command,stop=self.sim.step(action,fixed_base=fixed_base,substep_check=check,command_raw=raw_action)
        self.last_legal_torso_target=float(action[19])
        self.physics.flush()
        after=float(self.sim.data.time)
        if self.record_video:
            with timed(self, 'rendering'):
                frames=self.sim.render(self.config.width,self.config.height)
            with timed(self, 'recording'):
                if self.recorder.steps == 1: self.recorder.video_frame(self.last_video_frames)
                self.recorder.video_frame(self.video_frames(frames,after))
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
        if self.manip_base_target is not None:
            delta=self.manip_base_target-self.sim.robot.group('base')
            delta[2]=self.yaw_gap(self.manip_base_target[2],self.sim.robot.group('base')[2])
            limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
            yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a[:3]=delta/scale
        self.tick(a,fixed_base=False)

    def mobile_tick(self,planner,point,side,grip):
        """One synchronized base+arm waypoint increment; never writes qpos."""
        point=np.asarray(point)
        target=planner.base_target_world(point)
        base_gap=target-self.sim.robot.group('base'); base_gap[2]=self.yaw_gap(target[2],self.sim.robot.group('base')[2])
        arm_gap=point[3:10]-self.sim.robot.group(side+'_arm')
        measured_h=float(self.sim.robot.group('torso')[1])
        h_gap=float(point[10])-measured_h
        limit=min(self.sim.robot.config.max_base_delta,self.config.navigation_speed_m_s/self.sim.robot.config.control_hz)
        yaw_limit=min(self.sim.robot.config.max_yaw_delta,self.config.navigation_yaw_speed_rad_s/self.sim.robot.config.control_hz)
        scale=max(1.,np.linalg.norm(base_gap[:2])/limit,abs(base_gap[2])/yaw_limit,
                  np.max(np.abs(arm_gap))/(self.sim.robot.config.max_arm_delta*.9),
                  abs(h_gap)/planner.h_step_limit)
        a=self.sim.robot.neutral_action(); a[:3]=base_gap/scale
        offset=3 if side=='left' else 11; a[offset:offset+7]=arm_gap/scale
        a[10 if side=='left' else 18]=grip; a[19]=float(np.clip(measured_h+h_gap/scale,*self.sim.robot.config.torso_limits))
        if self.monitor:
            idle='right' if side=='left' else 'left'; i=11 if side=='left' else 3
            a[i:i+7]=np.asarray(self.monitor.initial['idle_arm'])-self.sim.robot.group(idle+'_arm')
        self.manip_base_target=target
        self.tick(a,fixed_base=False)
        return scale

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
            raise OperationFailure(diagnostic.get('failure_reason') or
                                   ('fov_constraint_failed' if result.status == 'fov_constraint_failed' else 'planning_no_solution'),diagnostic)
        return result

    def follow(self,planner,goal,side,grip):
        result=self.plan(planner,goal)
        self.follow_points(planner, result.positions, side, grip, goal)

    def follow_points(self,planner,positions,side,grip,goal=None):
        if not positions: raise OperationFailure('empty_trajectory')
        expected = ('base_x', 'base_y', 'base_theta', *(f'{side}_arm_{i}' for i in range(7)), 'torso_1')
        if tuple(planner.names) != expected or np.asarray(positions).shape[1:] != (11,):
            raise ValueError('mobile planner joint ordering mismatch')
        for point in positions:
            for _ in range(120):
                if self.mobile_tick(planner, point, side, grip) <= 1.: break
            else: raise OperationFailure('arm_tracking_timeout')
        goal_q=np.asarray(positions[-1])
        for _ in range(100):
            base_gap=planner.base_target_world(goal_q)-self.sim.robot.group('base')
            base_gap[2]=self.yaw_gap(planner.base_target_world(goal_q)[2],self.sim.robot.group('base')[2])
            if (np.max(np.abs(self.sim.robot.group(side+'_arm')-goal_q[3:10])) < .01
                and np.linalg.norm(base_gap[:2]) < self.config.arrival_tolerance_m
                and abs(base_gap[2]) < self.config.arrival_tolerance_rad): break
            self.mobile_tick(planner, goal_q, side, grip)
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

