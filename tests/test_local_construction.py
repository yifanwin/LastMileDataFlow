"""共享场景准备与资产测量，不依赖历史 Agent/DSL。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mujoco

from helpers import table_scene
from lastmile_dataflow.catalog.library import ThorLibrary
from lastmile_dataflow.io import file_digest
from lastmile_dataflow.runtime.preparation import prepare_scene, settle_scene, SettleConfig
from lastmile_dataflow.runtime.simulation import InitializationError


class LocalConstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)

    def tearDown(self):
        self.tmp.cleanup()


    def test_source_stability_optional_but_edit_scope_still_checked(self):
        original = mujoco.mj_objectVelocity

        def unstable_neighbor(model, data, kind, body, velocity, local):
            original(model, data, kind, body, velocity, local)
            if model.body(body).name == 'neighbor':
                velocity[3] = 100.

        with patch('mujoco.mj_objectVelocity', side_effect=unstable_neighbor):
            with self.assertRaises(InitializationError):
                prepare_scene(self.source, self.robot, base=[0,0,0])
            config = SettleConfig(require_source_stability=False, support_tolerance_m=.02)
            with prepare_scene(self.source, self.robot, base=[0,0,0], settle_config=config) as p:
                self.assertTrue(p.settling['valid'])
                self.assertFalse(p.settling['stable'])
                self.assertEqual(p.graph.stage, 'observed')
                self.assertEqual(p.graph_config.support_tolerance_m, .02)
                self.assertAlmostEqual(p.settling['simulated_s'], config.settle_s)
                target_only = settle_scene(p.sim, config, stability_instances={'target'})
                self.assertTrue(target_only['stable'])
                self.assertFalse(target_only['observed_scene_stable'])
                neighbour = settle_scene(p.sim, config, stability_instances={'neighbor'})
                self.assertFalse(neighbour['valid'])
        with self.assertRaises(ValueError):
            SettleConfig(require_source_stability='false')


    def test_asset_category_retrieval_measured_z_up_without_source_changes(self):
        root = self.root/'thor'; folder = root/'Kitchen Objects/Cup/Prefabs/Cup_fixture'; folder.mkdir(parents=True)
        source = folder/'Cup_fixture.xml'
        source.write_text('<mujoco><worldbody><body name="cup"><freejoint/><geom type="box" size=".1 .2 .15" mass="1"/></body></worldbody></mujoco>')
        before = file_digest(source)
        library = ThorLibrary(root)
        self.assertEqual(library.categories(), ['Cup'])
        assets = library.retrieve([{'categories': ['Cup'], 'intended_role': 'obstacle', 'size_constraints_m': {}}], self.root/'retrieval')
        description = assets.describe()
        self.assertEqual(len(description), 1, description)
        self.assertAlmostEqual(description[0]['size']['height'], .4)
        self.assertAlmostEqual(description[0]['size']['depth'], .3)
        self.assertEqual(description[0]['root_motion'], 'free')
        self.assertEqual(file_digest(source), before)
