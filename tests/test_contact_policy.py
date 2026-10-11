"""Small physics/interface regressions; not real RBY-1 task-success evidence."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mujoco
import numpy as np

from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig, freeze_config, TaskConfig
from lastmile_dataflow.io import read_json
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_sampling import robot_contacts
from lastmile_dataflow.stations.no_edit_statistics import aggregate
from lastmile_dataflow.validation.contacts import ContactStatistics, simulation_failure
from lastmile_dataflow.validation.lightweight import inspect_state
from lastmile_dataflow.validation.no_edit import attribute
from lastmile_dataflow.validation.pick import constraint_failure, evaluate_pick

CONTACT={'body_names':['robot_0/left_arm_3_link','table'], 'geom_names':['arm','table'],
         'distance_m':-.02, 'position_world':[0,0,0], 'normal_force_n':1000.,
         'time_s':.004, 'forbidden':True, 'robot_contact':True, 'kind':'environment_collision',
         'allowed_floor_base':False, 'intended_target_contact':False}


class ContactPolicyTests(unittest.TestCase):
    def test_runtime_overlap_telemetry_but_initialization_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=make_sim(d)
            adr=sim.model.jnt_qposadr[sim.model.joint('target_free').id]
            sim.data.qpos[adr+2]=.02
            mujoco.mj_forward(sim.model,sim.data)
            runtime=inspect_state(sim,CollectionConfig())
            self.assertTrue(runtime['valid'])
            self.assertLess(min(c['distance_m'] for c in runtime['contacts']),-.05)
            self.assertFalse(inspect_state(sim,CollectionConfig(),initialization=True)['valid'])
            sim.close()

    def test_nonfinite_and_mujoco_reset_warning_are_simulation_failures(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=make_sim(d)
            sim.data.qvel[0]=np.nan
            self.assertEqual(simulation_failure(sim),'nonfinite_physics')
            sim.data.qvel[0]=0
            sim.data.warning[int(mujoco.mjtWarning.mjWARN_BADQACC)].number=1
            self.assertEqual(simulation_failure(sim),'mujoco_warning')
            sim.close()

    def test_pair_tick_count_not_mesh_manifold_count(self):
        stats=ContactStatistics();stats.update([CONTACT,CONTACT])
        stats.update([CONTACT,dict(CONTACT,forbidden=False)])
        self.assertEqual(stats.metrics()['forbidden_contact_count'],2)
        self.assertEqual(stats.metrics()['contact_count'],4)
        for _ in range(100):stats.update([CONTACT])
        self.assertEqual(len(stats.examples),32)

    def test_runtime_observer_does_not_apply_initialization_depth_filter(self):
        shallow=dict(CONTACT,distance_m=-.0001)
        with patch('lastmile_dataflow.validation.contacts.contact_observations',return_value=[shallow]):
            self.assertEqual(robot_contacts(None),[])
            self.assertEqual(robot_contacts(None,initialization=False),[shallow])

    def test_contacts_do_not_mask_task_success_or_failure_cause(self):
        from test_stations import evidence
        initial,rows=evidence()
        for row in rows:row['illegal']=['self_collision','robot_environment_collision','nonfinger_target']
        self.assertEqual(evaluate_pick(rows,initial)['status'],'success')
        self.assertIsNone(constraint_failure(rows[0],initial))
        result=attribute('lift_contact_hold_or_slip',samples=rows,collisions=[CONTACT])
        self.assertNotEqual(result['category'],'Collision')
        rows[-1]['illegal']=['mujoco_warning']
        self.assertEqual(evaluate_pick(rows,initial)['status'],'simulation_failure')

    def test_open_contact_flags_do_not_fail_and_health_does(self):
        from lastmile_dataflow.validation.no_edit import evaluate_open
        task={'joint_goal':1.,'joint_initial':0.}
        rows=[{'time_s':t,'joint_value':1.,'finger_forces_n':{'1':1.,'2':1.},
               'illegal':['self_collision']} for t in (0.,1.)]
        self.assertEqual(evaluate_open(task,rows,.5)['status'],'success')
        rows[-1]['illegal']=['mujoco_warning']
        self.assertEqual(evaluate_open(task,rows,.5)['status'],'simulation_failure')

    def test_simulation_failure_excluded_from_task_rate(self):
        station={'station_id':'S0','geometry':'valid'}
        rows=aggregate([station],[{'station_id':'S0','status':'simulation_failure','attempt':'x'}],5)
        self.assertIsNone(rows[0]['success_rate']);self.assertEqual(rows[0]['terminal_trials'],0)

    def test_planner_runtime_keeps_stepping_after_environment_contact(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d)
            collection=CollectionConfig(record_video=False)
            sim.freeze(Path(d)/'scene')
            recorder=AttemptRecorder(d,freeze_config(robot,TaskConfig(),collection),attempt_id='contact')
            ctx=ContinuousContext(sim,recorder,NoEditConfig(),record_rgb=False,record_png=False)
            a=sim.robot.neutral_action()
            with patch('lastmile_dataflow.runtime.no_edit_execution.contact_observations',return_value=[CONTACT]) as contacts:
                ctx.tick(a);ctx.tick(a)
            self.assertEqual(recorder.steps,2)
            self.assertGreater(ctx.contact_stats.forbidden_contact_count,1)
            self.assertIn('fingers',contacts.call_args.kwargs)
            ctx.finish();recorder.finish('execution_complete','test',{});sim.close()


if __name__=='__main__':unittest.main()
