import math
import tempfile
import unittest
from pathlib import Path
import numpy as np

from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.construction.compiler import compile_sample
from lastmile_dataflow.construction.dsl import ExecutableDSL, SymbolicDSL
from lastmile_dataflow.construction.case_schema import CheckResult, Review
from lastmile_dataflow.runtime.preparation import prepare_scene
from lastmile_dataflow.runtime.case_edit_session import CaseEditSession


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source, robot = table_scene(self.root)
        self.prepared = prepare_scene(source, robot, base=[0, 0, 0])
        self.session = CaseEditSession(self.prepared)
        self.baseline = self.prepared.sim.state_vector().copy()

    def tearDown(self):
        self.session.close()
        self.prepared.close()
        self.tmp.cleanup()

    def test_pending_acceptance_and_no_accumulation(self):
        p = proposal(.2)
        exe = compile_sample(p, template(), self.prepared.graph)
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review', trial.to_dict())
        with self.assertRaises(ValueError):
            self.session.accept(Review(exe.sample_id, 'uncertain', [CheckResult('visible', 'unknown')]), self.root/'scene')
        self.session.accept(Review(exe.sample_id, 'pass', [CheckResult('visible', 'pass')]), self.root/'scene')
        self.assertTrue((self.root/'scene/model.mjb').exists())
        self.session.reject()
        np.testing.assert_array_equal(self.baseline, self.session.sim.state_vector())
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review')

    def test_furniture_and_target_group_no_intermediate_support_check(self):
        p = proposal()
        p = SymbolicDSL('group', p.bindings, [
            {'op': 'move', 'subject': '$support', 'search_space': {'frame': 'world',
             'xy': {'mode': 'uniform', 'x': [4, 4], 'y': [1, 1]}}}, *p.operations])
        exe = compile_sample(p, template(), self.prepared.graph)
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review', trial.to_dict())
        self.assertAlmostEqual(self.session.sim.data.xpos[self.session.sim.model.body('table').id, 0], 4)
        # Unedited neighbor may settle onto floor; no protected-neighbor failure.
        self.assertEqual(trial.after_graph.nodes['neighbor']['support_status'], 'pass')
        self.session.reject()
        self.assertAlmostEqual(self.session.sim.data.xpos[self.session.sim.model.body('table').id, 0], 2)

    def test_after_goal_failure_rolls_back_and_begin_guard(self):
        p = proposal()
        exe = compile_sample(p, template(), self.prepared.graph)
        pose = exe.to_dict()
        pose['operations'][0]['pose']['position'][0] = 10
        trial = self.session.execute(template(), p, ExecutableDSL.from_dict(pose))
        self.assertEqual(trial.status, 'rolled_back')
        self.assertIsNone(self.session.pending)
        np.testing.assert_array_equal(self.baseline, self.session.sim.state_vector())
        self.prepared.sim.begin()
        with self.assertRaises(RuntimeError):
            self.session.execute(template(), p, exe)
        # Closing releases resources without a reset, even after begin.
        self.session.close()
        self.assertTrue(self.prepared.sim.started)

    def test_before_invariant_not_applicable(self):
        t = template().to_dict()
        t['invariants'].append({'predicate': 'distance_xy', 'args': ['$target', '$support'], 'min': 5})
        p = proposal()
        exe = compile_sample(p, template(), self.prepared.graph)
        trial = self.session.execute(t, p, exe)
        self.assertEqual(trial.status, 'binding_not_applicable')
