"""Scene source / runtime / robot configuration separation and RoboCasa target mapping (no RoboCasa needed)."""

from types import SimpleNamespace
import unittest

from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.robots.registry import load_robot_config
from lastmile_dataflow.robots.robocasa_config import RoboCasaRobotConfig
from lastmile_dataflow.scenes.library import SceneRecipe, load_scene_recipe
from lastmile_dataflow.scenes.robocasa import RoboCasaSceneRecipe, load_robocasa_recipe, scene_id
from lastmile_dataflow.sim.robocasa_runtime import object_record


class ConfigSeparationTests(unittest.TestCase):
    def test_procthor_keeps_lmdf_runtime_and_scale(self):
        recipe = load_scene_recipe("configs/scenes/panda_omron.yaml")
        self.assertEqual((recipe.scene_source, recipe.runtime), ("procthor_10k", "lmdf"))
        robot = load_robot_config(recipe.robot_config)
        self.assertIsInstance(robot, PandaOmronConfig)
        self.assertEqual((robot.scale, robot.control_profile), (0.9, "robocasa-v1"))

    def test_robocasa_recipe_references_separate_robot_config(self):
        recipe = load_robocasa_recipe("configs/scenes/robocasa365_pick.yaml")
        self.assertEqual((recipe.scene_source, recipe.runtime), ("robocasa365", "robocasa"))
        self.assertEqual([(i.layout_id, i.style_id, i.seed) for i in recipe.instances], [(1, 1, 42), (2, 1, 43), (3, 1, 44)])
        robot = load_robot_config(recipe.robot_config)
        self.assertIsInstance(robot, RoboCasaRobotConfig)
        self.assertEqual((robot.embodiment, robot.scale, robot.controller), ("PandaOmron", 1.0, "default"))
        self.assertEqual(scene_id(recipe, recipe.instances[0]), "robocasa365/PickPlaceCounterToCabinet/layout1-style1-seed42")

    def test_mismatched_source_runtime_or_scale_is_rejected(self):
        with self.assertRaises(ValueError):
            SceneRecipe(dataset_dir="d", assets_dir="a", asset_index="i", robot_config="r", houses=[0], runtime="robocasa")
        with self.assertRaises(ValueError):
            RoboCasaSceneRecipe(robot_config="r", instances=[{"layout_id": 1, "style_id": 1, "seed": 1}], runtime="lmdf")
        with self.assertRaises(ValueError):
            RoboCasaRobotConfig(scale=0.9)
        with self.assertRaises(ValueError):
            PandaOmronConfig(model_path="m", scale=1.0)
        with self.assertRaises(ValueError):
            RoboCasaSceneRecipe(robot_config="r", instances=[{"layout_id": 1, "style_id": 1, "seed": 1}] * 2)


class TargetMappingTests(unittest.TestCase):
    def test_object_record_maps_native_name_to_body_geoms_and_asset(self):
        env = SimpleNamespace(
            objects={"obj": SimpleNamespace(root_body="obj_main", contact_geoms=["obj_g0", "obj_g1"])},
            object_cfgs=[
                {"name": "distr_counter", "info": {"cat": "apple"}},
                {"name": "obj", "graspable": True, "info": {"cat": "cup", "groups": ["cup"], "mjcf_path": "/a/cup_3/model.xml"}},
            ],
        )
        self.assertEqual(
            object_record(env, "obj"),
            {
                "name": "obj",
                "root_body": "obj_main",
                "contact_geoms": ["obj_g0", "obj_g1"],
                "category": "cup",
                "groups": ["cup"],
                "mjcf_path": "/a/cup_3/model.xml",
                "graspable": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
