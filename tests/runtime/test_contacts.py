"""接触遥测、物理异常和任务结果的区别。"""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.evaluation.statistics import aggregate
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution.context import ContinuousContext
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.outcomes.contacts import (
    ContactSampler,
    ContactStatistics,
    simulation_failure,
)
from lastmile_dataflow.runtime.outcomes.evaluation import attribute
from lastmile_dataflow.runtime.outcomes.health import inspect_state
from lastmile_dataflow.runtime.outcomes.pick import constraint_failure, evaluate_pick
from lastmile_dataflow.scenes.stations.sampling import robot_contacts
from tests.helpers import fixtures, make_sim

CONTACT = {
    "body_names": ["robot_0/left_arm_3_link", "table"],
    "geom_names": ["arm", "table"],
    "distance_m": -0.02,
    "position_world": [0, 0, 0],
    "normal_force_n": 1000.0,
    "time_s": 0.004,
    "forbidden": True,
    "robot_contact": True,
    "kind": "environment_collision",
    "allowed_floor_base": False,
    "intended_target_contact": False,
}


class ContactPolicyTests(unittest.TestCase):
    def pick_sim(self, directory):
        from lastmile_dataflow.runtime.simulation import Simulation
        source, robot = fixtures(directory)
        tree = ET.parse(robot.model_path)
        for side in ('left', 'right'):
            for finger in (1, 2):
                body = tree.find(f".//body[@name='robot_0/gripper_finger_{side[0]}{finger}']")
                body.set('name', f'robot_0/ee_finger_{side[0]}{finger}')
            arm = tree.find(f".//body[@name='robot_0/{side}_arm_6_link']")
            ET.SubElement(arm, 'site', name=f'robot_0/ee_site_{side[0]}')
        tree.write(robot.model_path)
        return Simulation.from_source(source, robot, target='target')

    def test_runtime_overlap_telemetry_but_initialization_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _ = make_sim(d)
            adr = sim.model.jnt_qposadr[sim.model.joint("target_free").id]
            sim.data.qpos[adr + 2] = 0.02
            mujoco.mj_forward(sim.model, sim.data)
            runtime = inspect_state(sim, CollectionConfig())
            self.assertTrue(runtime["valid"])
            self.assertLess(min(c["distance_m"] for c in runtime["contacts"]), -0.05)
            self.assertFalse(inspect_state(sim, CollectionConfig(), initialization=True)["valid"])
            sim.close()

    def test_nonfinite_and_mujoco_reset_warning_are_simulation_failures(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _ = make_sim(d)
            sim.data.qvel[0] = np.nan
            self.assertEqual(simulation_failure(sim), "nonfinite_physics")
            sim.data.qvel[0] = 0
            sim.data.warning[int(mujoco.mjtWarning.mjWARN_BADQACC)].number = 1
            self.assertEqual(simulation_failure(sim), "mujoco_warning")
            sim.close()

    def test_pair_tick_count_not_mesh_manifold_count(self):
        stats = ContactStatistics()
        stats.update([CONTACT, CONTACT])
        stats.update([CONTACT, dict(CONTACT, forbidden=False)])
        self.assertEqual(stats.metrics()["forbidden_contact_count"], 2)
        self.assertEqual(stats.metrics()["contact_count"], 4)
        for _ in range(100):
            stats.update([CONTACT])
        self.assertEqual(len(stats.examples), 32)

    def test_runtime_observer_does_not_apply_initialization_depth_filter(self):
        shallow = dict(CONTACT, distance_m=-0.0001)
        with patch(
            "lastmile_dataflow.runtime.outcomes.contacts.contact_observations", return_value=[shallow]
        ):
            self.assertEqual(robot_contacts(None), [])
            self.assertEqual(robot_contacts(None, initialization=False), [shallow])

    def test_contacts_do_not_mask_task_success_or_failure_cause(self):
        from tests.helpers import evidence

        initial, rows = evidence()
        for row in rows:
            row["illegal"] = ["self_collision", "robot_environment_collision", "nonfinger_target"]
        self.assertEqual(evaluate_pick(rows, initial)["status"], "success")
        self.assertIsNone(constraint_failure(rows[0], initial))
        result = attribute("lift_contact_hold_or_slip", samples=rows, collisions=[CONTACT])
        self.assertNotEqual(result["category"], "Collision")
        rows[-1]["illegal"] = ["mujoco_warning"]
        self.assertEqual(evaluate_pick(rows, initial)["status"], "simulation_failure")

    def test_open_contact_flags_do_not_fail_and_health_does(self):
        from lastmile_dataflow.runtime.outcomes.evaluation import evaluate_open

        task = {"joint_goal": 1.0, "joint_initial": 0.0}
        rows = [
            {
                "time_s": t,
                "joint_value": 1.0,
                "finger_forces_n": {"1": 1.0, "2": 1.0},
                "illegal": ["self_collision"],
            }
            for t in (0.0, 1.0)
        ]
        self.assertEqual(evaluate_open(task, rows, 0.5)["status"], "success")
        rows[-1]["illegal"] = ["mujoco_warning"]
        self.assertEqual(evaluate_open(task, rows, 0.5)["status"], "simulation_failure")

    def test_simulation_failure_excluded_from_task_rate(self):
        station = {"station_id": "S0", "geometry": "valid"}
        rows = aggregate([station], [{"station_id": "S0", "status": "simulation_failure", "attempt": "x"}], 5)
        self.assertIsNone(rows[0]["success_rate"])
        self.assertEqual(rows[0]["terminal_trials"], 0)

    def test_planner_runtime_keeps_stepping_after_environment_contact(self):
        with tempfile.TemporaryDirectory() as d:
            sim, robot = make_sim(d)
            collection = CollectionConfig(record_video=False, record_images=False)
            sim.freeze(Path(d) / "scene")
            recorder = AttemptRecorder(
                d, {"robot": asdict(robot), "collection": asdict(collection)}, attempt_id="contact"
            )
            recorder.bind_simulation(sim)
            ctx = ContinuousContext(sim, recorder, ExecutionConfig())
            a = sim.robot.neutral_action()
            frame = ContactSampler(sim).capture()
            with patch.object(frame, 'observations', return_value=[CONTACT]), patch.object(
                ctx.contact_sampler, 'capture', return_value=frame
            ) as contacts:
                # Supply a forbidden pair while keeping the numerical contact
                # frame separate from its optional detailed example dictionary.
                from lastmile_dataflow.runtime.outcomes.contacts import ContactPair
                pair = ContactPair((0, 1), tuple(CONTACT['body_names']), (0, 1),
                                   tuple(CONTACT['geom_names']), (True, False), False, False)
                frame.contacts = [(0, pair, 1000., True, False, True)]
                ctx.tick(a)
                ctx.tick(a)
            self.assertEqual(recorder.steps, 2)
            self.assertGreater(ctx.contact_stats.forbidden_contact_count, 1)
            self.assertIn("fingers", contacts.call_args.kwargs)
            ctx.finish()
            recorder.finish("execution_complete", "test")
            sim.close()

    def test_shared_contact_frame_preserves_pick_facts_and_counts_without_second_force_read(self):
        from lastmile_dataflow.runtime.outcomes.pick import PickMonitor
        with tempfile.TemporaryDirectory() as directory:
            sim = self.pick_sim(directory)
            self.addCleanup(sim.close)
            adr = sim.model.jnt_qposadr[sim.model.joint('target_free').id]
            sim.data.qpos[adr + 2] = .02
            mujoco.mj_forward(sim.model, sim.data)
            monitor = PickMonitor(sim, 'left')
            expected = monitor.sample()
            sampler = ContactSampler(sim)
            with patch('mujoco.mj_contactForce', wraps=mujoco.mj_contactForce) as force:
                frame = sampler.capture(target_bodies=monitor.target_bodies, fingers=monitor.allowed_target_fingers)
                facts = monitor.sample(frame)
                stats = ContactStatistics()
                stats.update_frame(frame)
                details = frame.observations()
            self.assertGreater(len(details), 0)
            self.assertEqual(force.call_count, len(details))
            self.assertEqual(facts, expected)
            self.assertTrue(facts['support_contact'])
            reference = ContactStatistics()
            reference.update(details)
            self.assertEqual(stats.metrics(), reference.metrics())
            self.assertEqual(stats.examples, reference.examples)

    def test_no_trace_skips_serialization_but_keeps_physics_samples(self):
        from lastmile_dataflow.runtime.outcomes.pick import PickMonitor
        with tempfile.TemporaryDirectory() as directory:
            sim = self.pick_sim(directory)
            self.addCleanup(sim.close)
            recorder = AttemptRecorder(directory, {'collection': asdict(CollectionConfig(
                record_images=False, record_video=False, record_physics=False))})
            recorder.bind_simulation(sim)
            ctx = ContinuousContext(sim, recorder, ExecutionConfig())
            self.addCleanup(ctx.finish)
            ctx.monitor = PickMonitor(sim, 'left')
            ctx.monitor.initial.update(workspace_center_xy=[0., 0.], workspace_radius_m=2.)
            with patch('lastmile_dataflow.runtime.execution.context.canonical',
                       side_effect=AssertionError('trace disabled')):
                ctx.tick(sim.robot.neutral_action())
            self.assertEqual(len(ctx.samples), 12)
            self.assertAlmostEqual(ctx.samples[-1]['time_s'], .048)
            self.assertFalse((recorder.path / 'physics.jsonl.gz').exists())
