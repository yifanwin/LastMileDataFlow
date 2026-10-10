import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from lastmile_dataflow.robots.action import InvalidAction
from lastmile_dataflow.robots.torso_command import bounded_torso_command, torso_feedback_margin, FEEDBACK_TOLERANCE_RAD
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext, OperationFailure
from lastmile_dataflow.stations.no_edit_config import NoEditConfig


class TorsoCommandTests(unittest.TestCase):
    def test_recorded_incident_and_limits(self):
        self.assertEqual(bounded_torso_command(.739327605117437, (0,.738)), .738)
        self.assertLess(torso_feedback_margin(.739327605117437,(0,.738)),FEEDBACK_TOLERANCE_RAD)
        self.assertEqual(bounded_torso_command(-.001,(0,.738)),0)
        self.assertEqual(bounded_torso_command(.4,(0,.738)),.4)
        for h in [.75,-.01,float('nan'),float('inf')]:
            with self.assertRaises(InvalidAction): bounded_torso_command(h,(0,.738))

    def test_hold_uses_command_not_feedback(self):
        ctx=ContinuousContext.__new__(ContinuousContext)
        ctx.sim=SimpleNamespace(robot=SimpleNamespace(group=lambda _: [0,.739327605117437],
                                                    config=SimpleNamespace(torso_limits=(0,.738))))
        ctx.last_legal_torso_target=.737261950969696
        self.assertEqual(ctx.hold_torso_target(),.737261950969696)
        ctx.last_legal_torso_target=None
        self.assertEqual(ctx.hold_torso_target(),.738)

    def test_tick_clips_records_and_checks_feedback(self):
        for feedback, fails in [(.739327605117437,False),(.745,True)]:
            with self.subTest(feedback=feedback), tempfile.TemporaryDirectory() as directory:
                ctx=ContinuousContext.__new__(ContinuousContext)
                ctx.config=NoEditConfig(planner_backend='curobo_v2_v080',max_attempts=5)
                ctx.phase='close';ctx.deadline=lambda:None;ctx.begin=lambda:None
                ctx.state=lambda:{'time_s':0.};ctx.monitor=None;ctx.collisions=[];ctx.fingers=set();ctx.target_bodies=set()
                ctx.physics=io.BytesIO();ctx.replay=[];ctx.record_rgb=False;ctx.record_png=False
                saved=[]
                ctx.recorder=SimpleNamespace(path=Path(directory),steps=0,record_step=lambda **kw:saved.append(kw))
                data=SimpleNamespace(time=0.,qpos=np.array([feedback]),qvel=np.zeros(1),qacc=np.zeros(1),warning=[])
                robot=SimpleNamespace(config=SimpleNamespace(torso_limits=(0,.738)),group=lambda _:np.array([0,feedback]))
                submitted=[]
                def step(action,**kw):
                    submitted.append(action.copy());check=kw['substep_check']()
                    return {'submitted_action':action.tolist()},None if check['valid'] else check
                ctx.sim=SimpleNamespace(data=data,robot=robot,model=None,step=step)
                action=np.zeros(20);action[19]=.739327605117437
                with patch('lastmile_dataflow.runtime.no_edit_execution.sanitize_action',side_effect=lambda robot,a,**kw:(a,[])), patch('lastmile_dataflow.runtime.no_edit_execution.mujoco.mj_forward'),patch('lastmile_dataflow.runtime.no_edit_execution.robot_contacts',return_value=[]):
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

if __name__=='__main__':unittest.main()
