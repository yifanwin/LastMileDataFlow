import math
import numpy as np
import tempfile
import unittest
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.construction.compiler import compile_candidates, compile_sample, SearchExhausted
from lastmile_dataflow.construction.dsl import SymbolicDSL
from lastmile_dataflow.runtime.preparation import prepare_scene


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        source, robot = table_scene(self.tmp.name)
        self.prepared = prepare_scene(source, robot, base=[0, 0, 0])

    def tearDown(self):
        self.prepared.close()
        self.tmp.cleanup()

    def test_replay_and_height_joint_footprint(self):
        a = compile_candidates(proposal(), template(), self.prepared.graph, seed=3)
        b = compile_candidates(proposal(), template(), self.prepared.graph, seed=3)
        self.assertEqual(a.attempted_count, 16)
        self.assertEqual([c.to_dict() for c in a.candidates], [c.to_dict() for c in b.candidates])
        for c in a.candidates:
            xyz = c.operations[0]['pose']['position']
            self.assertTrue(1.45 <= xyz[0] <= 2.55)
            self.assertAlmostEqual(xyz[2], .493, places=3)
        self.assertAlmostEqual(self.prepared.graph.nodes['target']['pose']['position'][0], 2)

    def test_support_moved_before_placement(self):
        p = proposal()
        furniture = {'op': 'move', 'subject': '$support', 'search_space': {
            'frame': 'world', 'xy': {'mode': 'uniform', 'x': [4, 4], 'y': [1, 1]}}}
        p = SymbolicDSL('p', p.bindings, [furniture, *p.operations])
        c = compile_sample(p, template(), self.prepared.graph)
        self.assertGreater(c.operations[1]['pose']['position'][0], 3.4)
        self.assertGreater(c.operations[1]['pose']['position'][1], .5)

    def test_large_rotation_and_translation_not_penalized(self):
        p = proposal()
        p = SymbolicDSL('p', p.bindings, [{'op': 'rotate', 'subject': '$target',
                       'axis': [0, 0, 1], 'angle': {'value': math.pi}}])
        c = compile_sample(p, template(), self.prepared.graph)
        self.assertAlmostEqual(abs(c.operations[0]['pose']['quaternion_wxyz'][3]), 1)

    def test_nonzero_initial_rotation_world_and_object_axis_order(self):
        graph = self.prepared.graph.to_dict()
        graph['nodes']['target']['pose']['quaternion_wxyz'] = [2**-.5, 0, 0, 2**-.5]
        for frame, expected in [('world', [.5,.5,-.5,.5]), ('object', [.5,.5,.5,.5])]:
            p = SymbolicDSL('p', proposal().bindings, [{'op': 'rotate', 'subject': '$target',
                'axis': [1,0,0], 'angle': {'value': math.pi/2}, 'frame': frame}])
            concrete = compile_sample(p, template(), graph).operations[0]['pose']
            np.testing.assert_allclose(concrete['quaternion_wxyz'], expected, atol=1e-12)
            self.assertEqual(concrete['position'], graph['nodes']['target']['pose']['position'])

    def test_world_frame_cannot_silently_override_support_local_xy(self):
        value = proposal().to_dict()
        value['operations'][0]['search_space']['frame'] = 'world'
        with self.assertRaises(ValueError):
            SymbolicDSL.from_dict(value)

    def test_search_exhaustion_not_mathematical_unsat(self):
        batch = compile_candidates(proposal(10), template(), self.prepared.graph, max_samples=2)
        self.assertEqual(batch.attempted_count, 2)
        self.assertFalse(batch.candidates)
        self.assertEqual(batch.failures[0]['status'], 'search_exhausted')
