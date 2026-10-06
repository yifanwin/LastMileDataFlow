import tempfile
import time
import unittest
from pathlib import Path

import mujoco
import numpy as np
from case_edit_helpers import table_scene
from lastmile_dataflow.construction.case_schema import Condition
from lastmile_dataflow.runtime.preparation import prepare_scene, settle_scene
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.graph import SceneGraph, build_scene_graph
from lastmile_dataflow.validation.predicates import evaluate_condition


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)
        self.original = Path(self.source.xml_path).read_bytes()
        self.prepared = prepare_scene(self.source, self.robot, base=[0, 0, 0])

    def tearDown(self):
        self.prepared.close()
        self.tmp.cleanup()

    def check(self, predicate, args, **kwargs):
        return evaluate_condition(Condition(predicate, args, **kwargs), self.prepared.graph)

    def test_unbound_real_settling_and_baseline(self):
        self.assertIsNone(self.prepared.sim.target_id)
        self.assertFalse(self.prepared.sim.started)
        self.assertGreater(self.prepared.sim.data.time, .9)
        self.assertGreater(self.prepared.settling['steps'], 100)
        self.prepared.save(self.root / 'baseline')
        sim = Simulation.from_snapshot(self.root / 'baseline/scene', self.robot)
        try:
            np.testing.assert_array_equal(sim.data.qpos, self.prepared.sim.data.qpos)
            self.assertIsNone(sim.target_id)
        finally:
            sim.close()
        self.assertEqual(Path(self.source.xml_path).read_bytes(), self.original)

    def test_actual_support_and_surface(self):
        self.assertEqual(self.check('supported_by', ['target', 'table'], value=True).status, 'pass')
        regions = [n for n in self.prepared.graph.nodes.values() if n['kind'] == 'region']
        self.assertAlmostEqual(regions[0]['height'], .44)
        self.assertEqual(self.prepared.graph.nodes['target']['category_status'], 'unknown')
        # Parent metadata is not a contact or a support proof.
        for item in self.prepared.sim.catalog:
            if item['instance_id'] == 'target':
                item['parent_instance_id'] = 'table'
                item['body_id'] = 9999
        j = self.prepared.sim.model.joint('target_free').qposadr[0]
        self.prepared.sim.data.qpos[j+2] = 2
        mujoco.mj_forward(self.prepared.sim.model, self.prepared.sim.data)
        self.prepared.refresh()
        self.assertEqual(self.check('supported_by', ['target', 'table'], value=True).status, 'fail')
        self.assertEqual(self.prepared.graph.nodes['target']['parent_hint'], 'table')

    def test_lazy_measurements_and_unknown(self):
        self.assertEqual(self.check('distance_xy', ['station_start', 'target'], range=[1.9, 2.1]).status, 'pass')
        self.assertEqual(self.check('distance_3d', ['station_start', 'target'], range=[2., 2.2]).status, 'pass')
        # Features use symbolic references; literal names remain stable graph IDs.
        c = Condition('direction_angle', ['$target.x_axis', {'vector': [1, 0, 0], 'frame': 'world'}], range=[0, .1])
        self.assertEqual(evaluate_condition(c, self.prepared.graph, bindings={'target': 'target'}).status, 'pass')
        c = Condition('direction_angle', ['$target.direction', {'vector': [1, 0, 0], 'frame': 'world'}], range=[0, .1])
        self.assertEqual(evaluate_condition(c, self.prepared.graph, bindings={'target': 'target'}).status, 'unknown')
        self.assertEqual(self.check('supported', ['missing'], value=False).status, 'fail')
        rid = next(k for k, n in self.prepared.graph.nodes.items() if n['kind'] == 'region')
        self.assertEqual(self.check('inside_region', ['target', rid], value=True).status, 'pass')
        value = self.prepared.graph.to_dict()
        value['nodes']['target']['footprint_world'][0][0] = 10
        self.assertEqual(evaluate_condition(Condition('inside_region', ['target', rid], value=True), value).status, 'fail')
        self.assertEqual(SceneGraph.from_dict(self.prepared.graph.to_dict()).to_dict(), self.prepared.graph.to_dict())
        self.assertFalse(any(e['predicate'].startswith('distance') for e in self.prepared.graph.edges))

    def test_guards_and_deadline(self):
        with self.assertRaises(TimeoutError):
            settle_scene(self.prepared.sim, deadline=time.monotonic()-1)
        self.prepared.sim.begin()
        with self.assertRaises(RuntimeError):
            self.prepared.refresh()
        with self.assertRaises(RuntimeError):
            settle_scene(self.prepared.sim)


if __name__ == '__main__':
    unittest.main()
