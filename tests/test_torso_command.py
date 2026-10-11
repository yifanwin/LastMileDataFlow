import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from lastmile_dataflow.robots.action import InvalidAction
from lastmile_dataflow.robots.torso_command import (
    bounded_torso_command, torso_feedback_margin, FEEDBACK_TOLERANCE_RAD, TORSO_SEVERE_FEEDBACK_RAD)
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext, OperationFailure, navigate
from lastmile_dataflow.stations.no_edit_config import NoEditConfig


class TorsoCommandTests(unittest.TestCase):
    def test_recorded_incident_and_limits(self):
        self.assertEqual(bounded_torso_command(.739327605117437, (0,.738)), .738)
        self.assertLess(torso_feedback_margin(.739327605117437,(0,.738)),FEEDBACK_TOLERANCE_RAD)
        self.assertEqual(bounded_torso_command(-.001,(0,.738)),0)
        self.assertEqual(bounded_torso_command(.4,(0,.738)),.4)
        for h in [.75,-.01,float('nan'),float('inf')]:
            with self.assertRaises(InvalidAction): bounded_torso_command(h,(0,.738))

    def test_severe_guard_is_far_above_the_recorded_tolerance(self):
        # record_only semantics: a small excursion is telemetry, a gross one stops.
        self.assertGreater(TORSO_SEVERE_FEEDBACK_RAD, FEEDBACK_TOLERANCE_RAD*10)
        # the exact excursion observed in the 20261010 221404 run must not stop.
        self.assertLess(torso_feedback_margin(-.0030406353838408396,(0,.738)), TORSO_SEVERE_FEEDBACK_RAD)

    def test_hold_uses_command_not_feedback(self):
        ctx=ContinuousContext.__new__(ContinuousContext)
        ctx.sim=SimpleNamespace(robot=SimpleNamespace(group=lambda _: [0,.739327605117437],
                                                    config=SimpleNamespace(torso_limits=(0,.738))))
        ctx.last_legal_torso_target=.737261950969696
        self.assertEqual(ctx.hold_torso_target(),.737261950969696)
        ctx.last_legal_torso_target=None
        self.assertEqual(ctx.hold_torso_target(),.738)

    def test_tick_clips_records_and_checks_feedback(self):
        # (feedback, expect_stop): a mild overshoot is recorded (record_only), a
        # gross one still stops the attempt.
        for feedback, fails in [(.739327605117437,False),(.745,False),(.80,True)]:
            with self.subTest(feedback=feedback), tempfile.TemporaryDirectory() as directory:
                ctx=ContinuousContext.__new__(ContinuousContext)
                ctx.config=NoEditConfig(planner_backend='curobo_v2_v080',max_attempts=5)
                ctx.phase='close';ctx.deadline=lambda:None;ctx.begin=lambda:None
                from lastmile_dataflow.validation.contacts import ContactStatistics
                ctx.contact_stats=ContactStatistics()
                ctx.state=lambda:{'time_s':0.};ctx.monitor=None;ctx.collisions=[];ctx.fingers=set();ctx.target_bodies=set()
                ctx.physics=io.BytesIO();ctx.replay=[];ctx.record_rgb=False;ctx.record_png=False
                saved=[]
                ctx.recorder=SimpleNamespace(path=Path(directory),steps=0,record_step=lambda **kw:saved.append(kw))
                data=SimpleNamespace(time=0.,qpos=np.array([feedback]),qvel=np.zeros(1),qacc=np.zeros(1),ctrl=np.zeros(1),warning=[])
                robot=SimpleNamespace(config=SimpleNamespace(torso_limits=(0,.738)),group=lambda _:np.array([0,feedback]))
                submitted=[]
                def step(action,**kw):
                    submitted.append(action.copy());check=kw['substep_check']()
                    return {'submitted_action':action.tolist()},None if check['valid'] else check
                ctx.sim=SimpleNamespace(data=data,robot=robot,model=None,step=step)
                action=np.zeros(20);action[19]=.739327605117437
                with patch('lastmile_dataflow.runtime.no_edit_execution.sanitize_action',side_effect=lambda robot,a,**kw:(a,[])), patch('lastmile_dataflow.runtime.no_edit_execution.mujoco.mj_forward'),patch('lastmile_dataflow.runtime.no_edit_execution.contact_observations',return_value=[]):
                    if fails:
                        with self.assertRaisesRegex(OperationFailure,'torso_feedback_limit'):ctx.tick(action)
                    else:ctx.tick(action)
                self.assertEqual(submitted[0][19],.738)
                self.assertEqual(action[19],.739327605117437)
                self.assertEqual(saved[0]['raw_action'][19],action[19])
                self.assertEqual(ctx.last_legal_torso_target,.738)
                event=json.loads((Path(directory)/'torso_command_adjustments.jsonl').read_text())
                self.assertEqual(event['submitted_h'],.738)
                row=json.loads(ctx.physics.getvalue())
                self.assertEqual(row['torso_feedback_tolerance_rad'],.003)
                # the raw measurement is still recorded even when it does not stop.
                self.assertAlmostEqual(row['torso_limit_excess_rad'],max(0.,feedback-.738))


class NavigateTorsoLoopTests(unittest.TestCase):
    """navigate() must command the same closed-loop torso hold as arm_tick."""
    def fake_ctx(self, hold_value):
        state={'base':np.array([0.,0.,0.])}
        ticks=[]
        config=SimpleNamespace(max_navigation_steps=2000,navigation_speed_m_s=.2,
                               navigation_yaw_speed_rad_s=.4,arrival_tolerance_m=.015,
                               arrival_tolerance_rad=.025,planner_backend='curobo_v2_v080')
        robot=SimpleNamespace(config=SimpleNamespace(control_hz=20.,max_base_delta=.1,max_yaw_delta=.2),
                              neutral_action=lambda: np.zeros(20))
        def group(name):
            if name=='base': return state['base'].copy()
            if name=='torso': return np.array([0.,.0,-.0,.0,.0,.0])
            return np.zeros(7)
        robot.group=group
        ctx=SimpleNamespace(sim=SimpleNamespace(robot=robot),config=config,yaw_gap=lambda t,c:t-c,
                            hold_torso_target=lambda: hold_value,phase='navigation')
        def tick(a,**kw):
            ticks.append(a.copy()); state['base']=state['base']+a[:3]
        ctx.tick=tick
        return ctx,ticks

    def test_navigation_and_arrival_hold_the_last_legal_torso_target(self):
        ctx,ticks=self.fake_ctx(.4717424850553562)
        path={'xy':[[1.,0.],[2.,0.]]}
        goal={'base':[2.,0.,0.]}
        result=navigate(ctx,path,goal)
        self.assertEqual(result['status'],'success')
        self.assertTrue(ticks)
        # neutral_action() echoes ctrl (0.0 here); navigate must override it with
        # the closed-loop hold, not leave the open-loop value.
        self.assertTrue(all(a[19]==.4717424850553562 for a in ticks),[a[19] for a in ticks])

    def test_navigate_still_works_without_a_hold_hook(self):
        ctx,ticks=self.fake_ctx(0.)
        del ctx.hold_torso_target
        result=navigate(ctx,{'xy':[[1.,0.],[2.,0.]]},{'base':[2.,0.,0.]})
        self.assertEqual(result['status'],'success')
        self.assertTrue(all(a[19]==0. for a in ticks))


if __name__=='__main__':unittest.main()
