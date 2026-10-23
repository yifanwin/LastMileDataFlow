"""站位采样、初始化过滤与覆盖压缩。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.augmentation.selection import coverage_select
from lastmile_dataflow.scenes.stations.sampling import disk_samples, sample_stations
from lastmile_dataflow.scenes.dataset_config import load_scene_recipe


class SamplingTests(unittest.TestCase):
    def test_unexpected_initialization_error_is_not_a_filtered_station(self):
        from types import SimpleNamespace

        probe = SimpleNamespace(close=Mock(), data=SimpleNamespace(warning=[]))
        failure = ValueError('broken camera configuration')
        with (
            patch('lastmile_dataflow.scenes.stations.sampling.clone_preparation', return_value=probe),
            patch('lastmile_dataflow.scenes.stations.sampling.initialize_station', side_effect=failure),
        ):
            with self.assertRaises(ValueError) as caught:
                sample_stations(None, {'anchor_world': [0, 0, 1]},
                                SimpleNamespace(radius_m=.1, spacing_m=.1, navigation_whole_body=False),
                                static_only=True)
        self.assertIs(caught.exception, failure)
        probe.close.assert_called_once()

    def test_invisible_physical_station_is_kept_and_visibility_recorded(self):
        from types import SimpleNamespace
        from lastmile_dataflow.scenes.stations.sampling import initialize_station
        sim = SimpleNamespace(started=False, closed=False, target_id=0,
            data=SimpleNamespace(qpos=np.zeros(1), qvel=np.zeros(1)), model=SimpleNamespace(nq=1, body=lambda _:SimpleNamespace(name='target')),
            robot=SimpleNamespace(config=SimpleNamespace(initial={'base':[0,0,0]}), initial_base_field='base',
                addresses={}, aim_station=lambda *args:{}, group=lambda _:np.zeros(3), observation_camera='head'))
        config = SimpleNamespace(min_target_pixels=24, width=8, height=8, require_target_visibility=False)
        with (patch('lastmile_dataflow.scenes.stations.sampling.robot_contacts',return_value=[]),
              patch('lastmile_dataflow.scenes.stations.sampling.floor_support',return_value=True),
              patch('lastmile_dataflow.scenes.stations.sampling.head_frame',
                    return_value=(None,None,{'visible_pixels':0}))):
            row = initialize_station(sim,{'xy':[0,0]},[1,0,1],config)
            self.assertEqual(row['geometry'],'valid')
            self.assertEqual(row['visibility']['visible_pixels'],0)
            config.require_target_visibility = True
            self.assertEqual(initialize_station(sim,{'xy':[0,0]},[1,0,1],config)['geometry'],'visibility_filtered')

    def test_embodiments_use_their_own_reach_and_base_quarter(self):
        from lastmile_dataflow.config import load_robot_config
        for recipe, robot in [('panda_omron_robocasa_val103','configs/robots/panda_omron_robocasa.json'),
                              ('val103_cup30_minimal_v3','configs/scene_datasets/rby1_position_head320.json')]:
            config = load_scene_recipe(f'configs/scene_datasets/{recipe}.json')
            body = load_robot_config(robot)
            self.assertEqual(config.geometry.radius_m, body.arm_working_reach_m)
            self.assertEqual(config.geometry.spacing_m, body.base_radius_m/4)
            self.assertEqual(config.station_selection, 'coverage')
            self.assertEqual(config.max_final_stations, 20)
            self.assertFalse(config.geometry.require_target_visibility)
            self.assertFalse(config.geometry.require_navigation_clearance)

    def test_invalid_parameters_rejected(self):
        for changes in ({"spacing_m": 0}, {"trials_per_station": True}, {"operations": ["place"]}):
            with self.assertRaises(ValueError):
                ExecutionConfig(**changes)

    def test_zero_optional_geometry_offsets_are_legal(self):
        c = ExecutionConfig(approach_offset_m=0.0, navigation_margin_m=0.0)
        self.assertEqual(c.approach_offset_m, 0.0)
        with self.assertRaises(ValueError):
            ExecutionConfig(navigation_margin_m=-0.01)

    def test_uniform_disk_and_reproducibility(self):
        rows = disk_samples([1, 2, 3], 2, 0.5)
        self.assertEqual(rows, disk_samples([1, 2, 3], 2, 0.5))
        self.assertTrue(all(np.linalg.norm(np.array(r["xy"]) - [1, 2]) <= 2 + 1e-9 for r in rows))
        self.assertEqual(len({tuple(r["grid_index"]) for r in rows}), len(rows))


class StationSelectionTests(unittest.TestCase):
    def test_coverage_not_prefix_and_preserves_range_and_bearing(self):
        rows = []
        for radius in (0.2, 0.5, 0.8):
            for angle in np.linspace(-np.pi, np.pi, 24, endpoint=False):
                rows.append(
                    {
                        "station_id": str(len(rows)),
                        "xy": [radius * np.cos(angle), radius * np.sin(angle)],
                        "geometry": "valid",
                    }
                )
        selected, info = coverage_select(rows, [0, 0], 20)
        self.assertEqual(len(selected), 20)
        self.assertNotEqual(info["selected_ids"], [s["station_id"] for s in rows[:20]])
        radius = np.linalg.norm([s["xy"] for s in selected], axis=1)
        self.assertAlmostEqual(radius.min(), 0.2)
        self.assertAlmostEqual(radius.max(), 0.8)
        theta = np.arctan2(*np.asarray([s["xy"] for s in selected]).T[::-1])
        self.assertEqual(len(set(((theta + np.pi) / (2 * np.pi) * 8).astype(int) % 8)), 8)
        self.assertEqual(len(info["discarded"]), 52)
        self.assertEqual(selected, coverage_select(rows, [0, 0], 20)[0])

    def test_filtered_points_never_reappear(self):
        rows = [
            {"station_id": "bad", "xy": [0, 0], "geometry": "geometry_filtered"},
            {"station_id": "good", "xy": [1, 0], "geometry": "valid"},
        ]
        self.assertEqual(coverage_select(rows, [0, 0], 20)[0], [rows[1]])

    def test_static_checks_every_candidate_without_renderer_and_fresh_sim(self):
        from types import SimpleNamespace

        clones = []

        def clone(_):
            probe = SimpleNamespace(close=lambda: None, data=SimpleNamespace(warning=[]))
            clones.append(probe)
            return probe

        task = {"anchor_world": [0, 0, 1]}
        config = SimpleNamespace(radius_m=0.3, spacing_m=0.1, navigation_whole_body=False)
        with (
            patch("lastmile_dataflow.scenes.stations.sampling.clone_preparation", side_effect=clone),
            patch(
                "lastmile_dataflow.scenes.stations.sampling.initialize_station",
                side_effect=lambda sim, station, *a, **kw: {**station, "geometry": "valid"},
            ) as init,
            patch("lastmile_dataflow.scenes.stations.sampling.mujoco.Renderer") as renderer,
        ):
            rows = sample_stations(None, task, config, static_only=True)
        self.assertEqual(init.call_count, len(rows))
        self.assertEqual(len(set(id(c) for c in clones)), len(rows))
        renderer.assert_not_called()
        self.assertTrue(all(call.kwargs["check_visibility"] is False for call in init.call_args_list))

    def test_solver_warning_cannot_be_a_valid_initialization(self):
        from types import SimpleNamespace

        def clone(_):
            return SimpleNamespace(
                close=lambda: None, data=SimpleNamespace(warning=[SimpleNamespace(number=0, lastinfo=0)])
            )

        def initialize(sim, station, *args, **kwargs):
            sim.data.warning[0].number += 1
            return {**station, "geometry": "valid"}

        with (
            patch("lastmile_dataflow.scenes.stations.sampling.clone_preparation", side_effect=clone),
            patch("lastmile_dataflow.scenes.stations.sampling.initialize_station", side_effect=initialize),
        ):
            rows = sample_stations(
                None,
                {"anchor_world": [0, 0, 1]},
                SimpleNamespace(radius_m=0.1, spacing_m=0.1, navigation_whole_body=False),
                static_only=True,
            )
        self.assertTrue(
            all(
                s["geometry"] == "geometry_filtered" and s["reason"] == "robot_initialization_solver_warning"
                for s in rows
            )
        )
