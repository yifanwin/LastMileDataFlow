"""The RL robot namespace comes from one place: mjlab's entity prefix plus the protocol namespace."""

from dataclasses import asdict
import unittest

from lastmile_dataflow.robots.interface import ROBOT_NAMESPACE
from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.station_map.rl.env.scene import ROBOT_ENTITY, ROBOT_PREFIX, EntityPandaConfig, RLSceneConfig


class RobotNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.scene = RLSceneConfig(
            data_root="data", scene_xml="s.xml", scene_metadata="m.json", robot_xml="r.xml", robot_scale=0.9, lift=0.3
        )

    def test_entity_prefix_wraps_the_protocol_namespace(self):
        self.assertEqual(ROBOT_PREFIX, ROBOT_ENTITY + "/" + ROBOT_NAMESPACE)
        self.assertEqual(ROBOT_PREFIX, "robot/robot_0/")

    def test_source_and_entity_configs_differ_only_in_namespace(self):
        source = self.scene.robot_config()
        entity = self.scene.robot_config(entity=True)
        self.assertIs(type(source), PandaOmronConfig)
        self.assertIsInstance(entity, EntityPandaConfig)
        self.assertEqual(source.namespace, ROBOT_NAMESPACE)
        self.assertEqual(entity.namespace, ROBOT_PREFIX)
        self.assertEqual(asdict(source), asdict(entity))
        self.assertEqual(entity.initial["lift"], 0.3)
        self.assertEqual(entity.initial["arm_q"]["right"], list(self.scene.home))


if __name__ == "__main__":
    unittest.main()
