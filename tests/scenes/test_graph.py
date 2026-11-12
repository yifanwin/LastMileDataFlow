"""静置后的支撑关系与场景图刷新。"""

import tempfile
import time
import unittest
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.runtime.construction.preparation import SettleConfig, preparation_guard, settle_scene
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.graph import build_scene_graph
from tests.helpers import settled_scene, table_scene


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)
        self.original = Path(self.source.xml_path).read_bytes()
        self.sim, self.settling = settled_scene(self.source, self.robot)
        self.config = SettleConfig().graph_config()
        self.graph = build_scene_graph(self.sim, stage="settled", config=self.config)

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def test_unbound_real_settling_and_baseline(self):
        self.assertTrue(self.settling.stable)
        self.assertIsNone(self.sim.target_id)
        self.assertFalse(self.sim.started)
        self.assertGreater(self.sim.data.time, 0.9)
        self.assertGreater(self.settling.steps, 100)
        self.sim.freeze(self.root / "baseline/scene")
        sim = Simulation.from_snapshot(self.root / "baseline/scene", self.robot)
        try:
            np.testing.assert_array_equal(sim.data.qpos, self.sim.data.qpos)
            self.assertIsNone(sim.target_id)
        finally:
            sim.close()
        self.assertEqual(Path(self.source.xml_path).read_bytes(), self.original)

    def test_actual_support_and_surface(self):
        self.assertTrue(
            any(e["predicate"] == "supported_by" and e["args"] == ["target", "table"] for e in self.graph.edges)
        )
        regions = [n for n in self.graph.nodes.values() if n["kind"] == "region"]
        self.assertAlmostEqual(regions[0]["height"], 0.44)
        self.assertEqual(self.graph.nodes["target"]["category_status"], "unknown")
        # Parent metadata is not a contact or a support proof.
        for item in self.sim.catalog:
            if item["instance_id"] == "target":
                item["parent_instance_id"] = "table"
                item["body_id"] = 9999
        j = self.sim.model.joint("target_free").qposadr[0]
        self.sim.data.qpos[j + 2] = 2
        mujoco.mj_forward(self.sim.model, self.sim.data)
        station = self.graph.nodes["station_start"]["pose"]
        self.graph = build_scene_graph(self.sim, revision=1, station=station, config=self.config)
        self.assertFalse(
            any(e["predicate"] == "supported_by" and e["args"] == ["target", "table"] for e in self.graph.edges)
        )
        self.assertEqual(self.graph.nodes["target"]["parent_hint"], "table")

    def test_guards_and_deadline(self):
        with self.assertRaises(TimeoutError):
            settle_scene(self.sim, deadline=time.monotonic() - 1)
        self.sim.begin()
        with self.assertRaises(RuntimeError):
            preparation_guard(self.sim)
        with self.assertRaises(RuntimeError):
            settle_scene(self.sim)
