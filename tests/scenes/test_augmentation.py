"""增强配置、放置几何、支撑覆盖和障碍物选择。"""

import itertools
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np

from lastmile_dataflow.io import write_json
from lastmile_dataflow.runtime.construction.preparation import (
    SettleConfig,
    settle_scene,
)
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.augmentation.checks import (
    load_support,
)
from lastmile_dataflow.scenes.augmentation.config import (
    ObstacleConfig,
    load_obstacle_config,
)
from lastmile_dataflow.scenes.augmentation.regions import (
    augmented_grid,
    rotated_placement,
    station_regions,
    support_regions,
)
from lastmile_dataflow.scenes.augmentation.sampling import (
    case_assets,
    sample_surface_point,
    substantial_asset,
)
from lastmile_dataflow.scenes.geometry import SupportRegion
from tests.helpers import ROOT


def config():
    return ObstacleConfig("baseline.json", "dataset", "objects", "target")


class ConfigTests(unittest.TestCase):
    def test_defaults_and_bad_count(self):
        self.assertEqual(config().obstacle_count, [1, 1])
        for count in ([0, 1], [1, 4], [3, 1]):
            with self.assertRaises(ValueError):
                replace(config(), obstacle_count=count)

    def test_mandatory_categories_cannot_silently_disappear(self):
        with self.assertRaises(ValueError):
            replace(config(), ground_categories=["Stool"])

    def test_unknown_keys_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            values = {
                "data_root": tmp,
                "baseline_config": "base.json",
                "dataset_dir": "data",
                "asset_library": "assets",
                "target_instance": "target",
                "input_snapshot": "../scene",
            }
            write_json(path, values)
            loaded = load_obstacle_config(path)
            self.assertEqual(loaded.baseline_config, str(ROOT / "base.json"))
            self.assertEqual(loaded.input_snapshot, str((Path(tmp) / "../scene").resolve()))
            write_json(path, {**values, "agent_calls": 1})
            with self.assertRaises(TypeError):
                load_obstacle_config(path)

    def test_nonfinite_budget_rejected(self):
        with self.assertRaises(ValueError):
            replace(config(), variant_timeout_s=float("inf"))


class GeometryTests(unittest.TestCase):
    def test_off_center_root_and_rotation(self):
        points = np.array([[1, 2, -0.2], [3, 4, 0.8]])
        for yaw in (0.0, np.pi / 2):
            pose, placed = rotated_placement(points, [7.0, 9.0], yaw, 0.5, 0.003)
            np.testing.assert_allclose((placed[:, :2].min(0) + placed[:, :2].max(0)) / 2, [7, 9])
            self.assertAlmostEqual(placed[:, 2].min(), 0.503)
            self.assertEqual(len(pose["quaternion_wxyz"]), 4)

    def test_midpoint_not_inherited_from_endpoints(self):
        xml = """<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/>
        <body name="block" pos="0 0 .1"><geom name="block_geom" type="box" size=".08 .08 .1"/></body></worldbody></mujoco>"""
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        stations = [
            {"station_id": "a", "xy": [-0.2, 0.0], "grid_index": [0, 0], "geometry": "valid"},
            {"station_id": "b", "xy": [0.2, 0.0], "grid_index": [1, 0], "geometry": "valid"},
        ]
        task = {"anchor_world": [0, 0, 0.5], "support_obstacles": []}
        regions = station_regions(
            sim, stations, task, ExecutionConfig(radius_m=0.6, map_resolution_m=0.025), 0.02, 0.12
        )
        self.assertEqual(len(regions["rejected_midpoints"]), 1)
        self.assertEqual(len(regions["points"]), 2)
        self.assertTrue(regions["edges"][0]["connected"])

    def test_no_all_pairs_midpoint_explosion(self):
        m = mujoco.MjModel.from_xml_string(
            '<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/></worldbody></mujoco>'
        )
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        stations = [
            {"station_id": "a", "xy": [-0.2, 0.0], "grid_index": [0, 0], "geometry": "valid"},
            {"station_id": "b", "xy": [0.2, 0.0], "grid_index": [2, 0], "geometry": "valid"},
        ]
        regions = station_regions(
            sim,
            stations,
            {"anchor_world": [0, 0, 0.5], "support_obstacles": []},
            ExecutionConfig(radius_m=0.6),
            0.02,
            0.12,
        )
        self.assertEqual(regions["edges"], [])

    def test_seat_above_chassis_still_blocks_navigation(self):
        m = mujoco.MjModel.from_xml_string(
            '<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/><body name="chair" pos="0 0 .7"><geom type="box" size=".2 .2 .1"/></body></worldbody></mujoco>'
        )
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        grid = augmented_grid(
            sim,
            {"anchor_world": [0, 0, 0.5], "support_obstacles": []},
            ExecutionConfig(radius_m=0.6),
            0.1,
            ["chair"],
        )
        self.assertFalse(grid.free[grid.cell([0, 0])])

    def test_surface_coverage_rechecked_after_settle_not_from_flag(self):
        xml = """<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/>
        <body name="table" pos="0 0 .4"><geom name="table_top" type="box" size=".3 .3 .04"/></body>
        <body name="procedural_obstacle_0" pos="0 0 .493"><freejoint name="object_free"/>
        <geom name="object_geom" type="box" size=".05 .05 .05" mass=".1"/></body></worldbody></mujoco>"""
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(
            model=m,
            data=d,
            started=False,
            closed=False,
            robot=SimpleNamespace(execution_started=False, config=SimpleNamespace(namespace="robot_0/")),
            state_vector=lambda: np.r_[d.qpos, d.qvel, d.ctrl],
        )
        settle_scene(sim, SettleConfig())
        p = {
            "kind": "surface",
            "support_geom": "table_top",
            "support_instance": "table",
            "support_height_m": 0.44,
            "surface_coverage_verified": True,
        }
        self.assertTrue(load_support(sim, [p], config())["valid"])
        q = int(m.jnt_qposadr[m.joint("object_free").id])
        d.qpos[q] = 0.29  # center is on table, but the settled footprint extends beyond edge
        mujoco.mj_forward(m, d)
        report = load_support(sim, [p], config())
        self.assertFalse(report["valid"])
        self.assertFalse(report["objects"][0]["coverage"])


class CaseTests(unittest.TestCase):
    def test_explicit_cases_and_unknown_nested_keys(self):
        for case, count in [("case1", [1, 3]), ("case2", [6, 12])]:
            c = load_obstacle_config(
                ROOT / f"configs/case_edits/procedural_obstacles_val103_cup30_{case}.json"
            )
            self.assertEqual(c.case_policy.case.lower(), case)
            self.assertEqual(c.obstacle_count, count)
            self.assertEqual(c.case_policy.min_prefilter_points, 5)
            with self.assertRaises(ValueError):
                replace(c, construction={"case": "Case9"})
            with self.assertRaises(TypeError):
                replace(c, construction={"case": c.case_policy.case, "typo": 1})

    def test_case1_exact_one_chair_and_surface_remainder(self):
        c = load_obstacle_config(ROOT / "configs/case_edits/procedural_obstacles_val103_cup30_case1.json")
        geometries = {
            name: {"asset_id": name, "category": cat} for name, cat in [("chair", "Chair"), ("cup", "Cup")]
        }
        selected = case_assets(
            np.random.default_rng(3),
            3,
            {"ground": ["Chair"], "surface": ["Cup"]},
            geometries,
            c.case_policy,
            c.mandatory_categories,
        )
        self.assertEqual([x["category"] for x in selected], ["Chair", "Cup", "Cup"])

    def test_case2_measurements_not_category_alone(self):
        c = load_obstacle_config(ROOT / "configs/case_edits/procedural_obstacles_val103_cup30_case2.json")
        g = {
            name: {"asset_id": name, "category": cat, "size": dict(width=w, depth=0.15, height=h)}
            for name, cat, w, h in [
                ("large", "Vase", 0.3, 0.35),
                ("small", "Vase", 0.1, 0.12),
                ("cup", "Cup", 0.1, 0.1),
            ]
        }
        self.assertTrue(substantial_asset(g["large"], c.case_policy))
        self.assertFalse(substantial_asset(g["small"], c.case_policy))
        selected = case_assets(
            np.random.default_rng(1), 12, {"ground": [], "surface": ["Vase", "Cup"]}, g, c.case_policy, []
        )
        self.assertEqual(len(selected), 12)
        self.assertGreaterEqual(sum(substantial_asset(a, c.case_policy) for a in selected), 2)

    def test_target_plane_not_leg_tops_or_lower_shelves(self):
        from types import SimpleNamespace

        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody><body name="table">
        <geom name="top" type="box" pos="0 0 .75" size=".5 .8 .02"/>
        <geom name="leg" type="box" pos=".4 .6 .35" size=".05 .05 .35"/>
        <geom name="shelf" type="box" pos="0 0 .3" size=".4 .7 .02"/>
        </body></worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        regions = support_regions(
            SimpleNamespace(model=model, data=data),
            {"anchor_world": [0, 0, 0.82], "support_obstacles": [{"body": "table"}]},
        )
        self.assertEqual([r.geom for r in regions], ["top"])

    def test_surface_sampler_conditions_on_size_and_free_region(self):
        c = load_obstacle_config(ROOT / "configs/case_edits/procedural_obstacles_val103_cup30_case2.json")
        region = SupportRegion(
            "r", "table", "top", "plane", [0, 0, 0.75], np.eye(3).tolist(), [-1, 1, -1, 1], 0.75, "fixture"
        )
        points = [
            {"point_id": str(i), "xy": xy, "region": region}
            for i, xy in enumerate([[-0.8, 0], [0, 0], [0.6, 0]])
        ]
        asset = {"local_points": np.array(list(itertools.product((-0.2, 0.2), (-0.2, 0.2), (0.0, 0.4))))}
        occupied = np.array([[[-0.4, -0.4, 0.75], [0.4, 0.4, 1.2]]])
        rng = np.random.default_rng(1)
        choices = [
            sample_surface_point(rng, asset, points, [], occupied, c.case_policy, 0.003)[0]["point_id"]
            for _ in range(100)
        ]
        self.assertGreater(choices.count("2"), 95)
        self.assertNotIn("0", choices)  # too close to edge for the complete rotated footprint
        huge = {"local_points": np.array([[-3, -3, 0], [3, 3, 0.4]])}
        self.assertIsNone(sample_surface_point(rng, huge, points, [], occupied, c.case_policy, 0.003)[0])
