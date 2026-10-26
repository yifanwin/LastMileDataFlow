"""构建事务、静置、源场景影响与回滚。"""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.data.rendering.edit_views import ViewConfig
from lastmile_dataflow.data.rendering.obstacle_views import render_obstacle_pair
from lastmile_dataflow.utils.io import write_json
from lastmile_dataflow.runtime.construction.preparation import (
    SettleConfig,
    prepare_scene,
    settle_scene,
)
from lastmile_dataflow.runtime.construction.session import (
    ObstacleEditSession,
    clone_preparation,
    matched_control,
)
from lastmile_dataflow.runtime.simulation import InitializationError, Simulation
from lastmile_dataflow.scenes.augmentation.checks import (
    collision_pairs,
    load_support,
    matched_source_effects,
)
from lastmile_dataflow.scenes.build.validation import PhysicalLimits
from lastmile_dataflow.scenes.graph import build_scene_graph
from lastmile_dataflow.scenes.loading.assets import EditAssetCatalog
from tests.helpers import fixtures, table_scene


def config():
    return PhysicalLimits()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source, robot = fixtures(self.root)
        self.sim = Simulation.from_source(source, robot, target="target")
        asset = self.root / "chair.xml"
        asset.write_text(
            '<mujoco><worldbody><body name="chair"><freejoint/><geom name="chair_geom" type="box" size=".2 .2 .5" mass="2"/><site name="receptacle_debug" type="box" size=".1 .1 .1" rgba="1 0 0 1"/></body></worldbody></mujoco>'
        )
        manifest = self.root / "assets.json"
        write_json(
            manifest,
            {
                "asset_catalog_version": "0.1",
                "assets": [
                    {"asset_id": "chair", "xml_path": str(asset), "root_body": "chair", "category": "Chair"}
                ],
            },
        )
        self.assets = EditAssetCatalog(manifest)
        self.session = ObstacleEditSession(
            self.sim, self.assets, SettleConfig(require_source_stability=False)
        )
        self.placement = {
            "asset_id": "chair",
            "kind": "ground",
            "pose": {"position": [4.0, 0.0, 0.503], "quaternion_wxyz": [1, 0, 0, 0]},
            "support_height_m": 0.0,
            "support_geom": None,
        }

    def tearDown(self):
        self.session.close()
        self.sim.close()
        self.tmp.cleanup()

    def test_add_real_body_and_migrate_target_state(self):
        candidate = self.session.candidate([self.placement])
        self.assertGreater(candidate.model.nq, self.sim.model.nq)
        self.assertEqual(candidate.model.body(candidate.target_id).name, "target")
        b = candidate.model.body("target").id
        np.testing.assert_array_equal(candidate.data.xpos[b], self.sim.data.xpos[self.sim.target_id])
        self.assertTrue(collision_pairs(candidate, [self.placement], config())["valid"])

    def test_forward_occurs_only_after_all_new_poses_are_set(self):
        placements = [copy.deepcopy(self.placement) for _ in range(6)]
        for i, p in enumerate(placements):
            p["pose"]["position"][0] += i
        native = mujoco.mj_forward
        calls = []

        def checked_forward(model, data):
            try:
                model.body("procedural_obstacle_0")
            except KeyError:
                return native(model, data)
            for i, p in enumerate(placements):
                b = model.body(f"procedural_obstacle_{i}").id
                q = int(model.jnt_qposadr[model.body_jntadr[b]])
                np.testing.assert_allclose(data.qpos[q : q + 3], p["pose"]["position"])
            calls.append(True)
            return native(model, data)

        with patch(
            "lastmile_dataflow.runtime.construction.session.mujoco.mj_forward", side_effect=checked_forward
        ):
            self.session.candidate(placements)
        self.assertEqual(len(calls), 1)

    def test_reject_keeps_exact_baseline_and_new_candidate_data(self):
        old = self.sim.state_vector().copy()
        a = self.session.candidate([self.placement])
        self.session.settle()
        self.session.reject()
        np.testing.assert_array_equal(old, self.sim.state_vector())
        b = self.session.candidate([self.placement])
        self.assertTrue(a.closed)
        self.assertIsNot(a.data, b.data)

    def test_post_begin_candidate_and_rollback_prohibited(self):
        candidate = self.session.candidate([self.placement])
        candidate.begin()
        with self.assertRaises(RuntimeError):
            self.session.reject()
        with self.assertRaises(RuntimeError):
            self.session.candidate([self.placement])

    def test_full_height_collision_catches_chair_body(self):
        p = copy.deepcopy(self.placement)
        p["pose"]["position"] = [2.0, 0.0, 0.5]
        candidate = self.session.candidate([p])
        self.assertFalse(collision_pairs(candidate, [p], config())["valid"])

    def test_load_bearing_floor_contact_after_actual_settle(self):
        candidate = self.session.candidate([self.placement])
        self.session.settle()
        self.assertTrue(load_support(candidate, [self.placement], config())["valid"])

    def test_matched_control_same_time_and_source_effects(self):
        candidate = self.session.candidate([self.placement])
        report = self.session.settle()
        control, _ = matched_control(self.sim, report["simulated_s"], self.session.config, None)
        try:
            self.assertAlmostEqual(control.data.time, candidate.data.time)
            self.assertTrue(matched_source_effects(control, candidate, config())["valid"])
            j = candidate.model.body("target").jntadr[0]
            q = int(candidate.model.jnt_qposadr[j])
            candidate.data.qpos[q] += 0.1
            mujoco.mj_forward(candidate.model, candidate.data)
            self.assertFalse(matched_source_effects(control, candidate, config())["valid"])
        finally:
            control.close()

    def test_wrong_control_time_rejected(self):
        candidate = self.session.candidate([self.placement])
        candidate.data.time = 1.0
        with self.assertRaises(ValueError):
            matched_source_effects(self.sim, candidate, config())

    def test_receptacle_site_hidden_without_changing_collision_geom(self):
        candidate = self.session.candidate([self.placement])
        site = candidate.model.site("procedural_obstacle_0/receptacle_debug").id
        self.assertEqual(candidate.model.site_group[site], 5)
        g = candidate.model.geom("procedural_obstacle_0/chair_geom").id
        self.assertEqual(candidate.model.geom_contype[g], 1)
        np.testing.assert_allclose(candidate.model.geom_size[g], [0.2, 0.2, 0.5])

    def test_three_added_bodies_and_mutual_collision(self):
        placements = [copy.deepcopy(self.placement) for _ in range(3)]
        for i, p in enumerate(placements):
            p["pose"]["position"][0] = 4.0 + i
        candidate = self.session.candidate(placements)
        self.assertTrue(collision_pairs(candidate, placements, config())["valid"])
        self.assertTrue(all(candidate.model.body(f"procedural_obstacle_{i}").id for i in range(3)))
        placements[2]["pose"]["position"][0] = 4.0
        candidate = self.session.candidate(placements)
        self.assertFalse(collision_pairs(candidate, placements, config())["valid"])

    @unittest.skipUnless(os.environ.get("CASE_EDIT_RENDER_TESTS") == "1", "opt-in actual OpenGL rendering")
    def test_observer_search_records_actual_segmentation(self):
        candidate = self.session.candidate([self.placement])
        before_graph = build_scene_graph(self.sim)
        after_graph = build_scene_graph(candidate)
        packet = render_obstacle_pair(
            self.sim,
            candidate,
            before_graph,
            after_graph,
            {"procedural_obstacle_0"},
            "target",
            set(),
            self.root / "rgb",
            sample_id="unit",
            config=ViewConfig(width=320, height=240),
            min_pixels=24,
        )
        for view in ("top", "oblique_a", "oblique_b"):
            counts = packet["visible_pixels_by_instance"]
            self.assertGreaterEqual(counts["before/" + view]["target"], 24)
            self.assertGreaterEqual(counts["after/" + view]["target"], 24)
            self.assertGreaterEqual(counts["after/" + view]["procedural_obstacle_0"], 24)
            self.assertEqual(counts["before/" + view]["procedural_obstacle_0"], 0)

    def test_clone_preserves_target_and_prohibits_post_begin(self):
        clone = clone_preparation(self.sim)
        self.assertEqual(clone.target_id, self.sim.target_id)
        clone.close()
        self.sim.begin()
        with self.assertRaises(RuntimeError):
            clone_preparation(self.sim)


class SettlingTests(unittest.TestCase):
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
            if model.body(body).name == "neighbor":
                velocity[3] = 100.0

        with patch("mujoco.mj_objectVelocity", side_effect=unstable_neighbor):
            with self.assertRaises(InitializationError):
                prepare_scene(self.source, self.robot, base=[0, 0, 0])
            config = SettleConfig(require_source_stability=False, support_tolerance_m=0.02)
            with prepare_scene(self.source, self.robot, base=[0, 0, 0], settle_config=config) as p:
                self.assertTrue(p.settling["valid"])
                self.assertFalse(p.settling["stable"])
                self.assertEqual(p.graph.stage, "observed")
                self.assertEqual(p.graph_config.support_tolerance_m, 0.02)
                self.assertAlmostEqual(p.settling["simulated_s"], config.settle_s)
                target_only = settle_scene(p.sim, config, stability_instances={"target"})
                self.assertTrue(target_only["stable"])
                self.assertFalse(target_only["observed_scene_stable"])
                neighbour = settle_scene(p.sim, config, stability_instances={"neighbor"})
                self.assertFalse(neighbour["valid"])
        with self.assertRaises(ValueError):
            SettleConfig(require_source_stability="false")
