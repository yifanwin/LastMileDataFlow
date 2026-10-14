"""Synthetic evidence/dispatch regressions, NOT real robotic success evidence."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.runtime.no_edit_evidence import hold_pick, resolve_timeout, timed, checkpoint_pick_success, PickEvidenceComplete
from lastmile_dataflow.validation.pick import PROTOCOL


def context(duration=2.08, *, support=False, gap=False, slip=False):
    initial = {'base':[0.,0.,0.], 'head':[0.,0.], 'idle_arm':[0.]*7,
               'height_m':.8,'target':'fixture','fingers':[1,2],
               'workspace_center_xy':[0.,0.],'workspace_radius_m':1.5}
    samples=[]
    for i in range(int(duration/.004)+1):
        pose=np.eye(4)
        if slip: pose[0,3]=i*.004
        samples.append({'time_s':i*.004,'phase':'hold','command_h':0.,'base':[0.,0.,0.],
                        'torso':[0.]*6,'head':[0.,0.],'idle_arm':[0.]*7,'target':'fixture',
                        'height_m':.9,'support_contact':support,'finger_forces_n':{'1':1.,'2':1.},
                        'relative_pose':pose.tolist(),'illegal':[], 'sampler':'force-aware-v3'})
    if gap: samples.pop(1)
    return SimpleNamespace(pick_initial=initial,samples=samples,phase='hold',timings={},arm_tick=Mock())


class EvidenceTests(unittest.TestCase):
    def test_already_successful_hold_stops_before_next_tick_deadline(self):
        ctx=context()
        ctx.arm_tick.side_effect=TimeoutError('attempt_wall_clock_budget')
        verdict=hold_pick(ctx,'right',np.zeros(7),0.)
        self.assertEqual(verdict['status'],'success')
        ctx.arm_tick.assert_not_called()

    def test_success_after_one_control_interval_does_not_wait_for_fifty(self):
        ctx=context(1.98)
        complete=context(2.04).samples
        ctx.arm_tick.side_effect=lambda *args: setattr(ctx,'samples',complete)
        self.assertEqual(hold_pick(ctx,'right',np.zeros(7),0.)['status'],'success')
        self.assertEqual(ctx.arm_tick.call_count,1)

    def test_lift_control_boundary_ends_at_first_complete_success(self):
        ctx=context();ctx.phase='lift_actual'
        with self.assertRaises(PickEvidenceComplete) as caught:checkpoint_pick_success(ctx)
        self.assertEqual(caught.exception.verdict['status'],'success')
        ctx.phase='pregrasp'
        checkpoint_pick_success(ctx)

    def test_timeout_preserves_termination_and_full_physical_success(self):
        termination,verdict=resolve_timeout(context(),{'operation':'pick'},'attempt_wall_clock_budget')
        self.assertEqual(termination['status'],'timeout')
        self.assertEqual(verdict['status'],'success')
        self.assertGreaterEqual(verdict['tail_held_s'],2.)

    def test_timeout_short_hold_not_promoted(self):
        self.assertEqual(resolve_timeout(context(1.86),{'operation':'pick'},'deadline')[1]['status'],'failure')

    def test_support_slip_and_physics_gap_never_promoted(self):
        for kwargs in ({'support':True},{'slip':True},{'gap':True}):
            with self.subTest(kwargs=kwargs):
                verdict=resolve_timeout(context(**kwargs),{'operation':'pick'},'deadline')[1]
                self.assertNotEqual(verdict['status'],'success')

    def test_complete_trace_constraints_checked_not_only_last_frame(self):
        ctx=context();ctx.samples[1]['head'][0]=.1
        self.assertEqual(resolve_timeout(ctx,{'operation':'pick'},'deadline')[1]['reason'],'head_drift')

    def test_missing_monitor_and_open_timeout_unknown(self):
        ctx=SimpleNamespace(phase='pregrasp',pick_initial=None)
        self.assertEqual(resolve_timeout(ctx,{'operation':'pick'},'deadline')[1]['status'],'unknown')
        self.assertEqual(resolve_timeout(ctx,{'operation':'open'},'deadline')[1]['status'],'unknown')

    def test_timers_account_exception(self):
        ctx=SimpleNamespace()
        with patch('lastmile_dataflow.runtime.no_edit_evidence.time.monotonic',side_effect=[1.,4.]),self.assertRaises(ValueError):
            with timed(ctx,'planning'):
                raise ValueError('fixture')
        self.assertEqual(ctx.timings['planning'],{'wall_s':3.,'calls':1})

    def test_hold_maximum_not_extended_when_no_success(self):
        ctx=context(support=True)
        self.assertEqual(hold_pick(ctx,'right',np.zeros(7),0.)['status'],'failure')
        self.assertEqual(ctx.arm_tick.call_count,50)

    def test_deadline_reached_in_incomplete_hold_is_separately_resolved(self):
        ctx=context(1.86);ctx.arm_tick.side_effect=TimeoutError('deadline')
        with self.assertRaises(TimeoutError):hold_pick(ctx,'right',np.zeros(7),0.)
        termination,verdict=resolve_timeout(ctx,{'operation':'pick'},'deadline')
        self.assertEqual(termination['status'],'timeout');self.assertNotEqual(verdict['status'],'success')

    def test_frozen_thresholds_not_changed(self):
        self.assertEqual(PROTOCOL['hold_s'],2.)
        self.assertEqual(PROTOCOL['lift_m'],.05)


    def test_timeout_runtime_finalizer_retains_task_success_and_control(self):
        from helpers import make_sim
        from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt
        from lastmile_dataflow.stations.no_edit_config import NoEditConfig
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.io import write_json,read_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);baseline,_=make_sim(root)
            self.addCleanup(baseline.close)
            def manipulation(ctx,*args):
                fixture=context();ctx.pick_initial=fixture.pick_initial;ctx.samples=fixture.samples
                ctx.phase='hold';ctx.active_control={'arm':'right','grasp_row':1,'grasp_source':'fixture'}
                ctx.sim.begin()
                raise TimeoutError('attempt_wall_clock_budget')
            with patch('lastmile_dataflow.runtime.no_edit_execution.place_frozen_station'), \
                 patch('lastmile_dataflow.runtime.no_edit_execution.manipulate',side_effect=manipulation):
                result=run_raw_attempt(baseline,{'target_body':'target','operation':'pick'},
                    {'station_id':'s','base':[0.,0.,0.]},[],root,NoEditConfig(third_person_enabled=False),
                    CollectionConfig(output_dir=d,record_video=False,record_images=False),root,'fixture-timeout',seed=1)
            self.assertEqual(result['status'],'success')
            self.assertEqual(result['execution_termination']['status'],'timeout')
            self.assertEqual(result['task_outcome']['status'],'success')
            self.assertEqual(result['control']['grasp_source'],'fixture')
            self.assertIn('recorder_finalize_wall_s',result['execution_diagnostics'])
            self.assertFalse(baseline.closed)


if __name__=='__main__':unittest.main()
