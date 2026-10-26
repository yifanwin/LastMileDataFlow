"""站位采样、初始化过滤与覆盖压缩。"""

import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.augmentation.selection import coverage_select
from lastmile_dataflow.scenes.stations.sampling import disk_samples, sample_stations, yaw_variants
from lastmile_dataflow.scenes.dataset_config import load_task_mode_recipe


class SamplingTests(unittest.TestCase):
    def test_explicit_yaw_survives_initialization_and_recheck(self):
        import tempfile
        from lastmile_dataflow.scenes.stations.sampling import initialize_station
        from tests.helpers import make_sim

        with tempfile.TemporaryDirectory() as directory:
            sim, _ = make_sim(directory)
            try:
                config = ExecutionConfig(radius_m=1., spacing_m=.25)
                nominal = {**disk_samples([0, 0, 1], 1., .25)[0],
                           'base': [0., -1., 0.], 'initial': sim.robot.config.initial}
                candidates = yaw_variants(nominal)
                self.assertEqual(len({s['station_id'] for s in candidates}), 3)
                self.assertEqual(len({s['xy_id'] for s in candidates}), 1)
                source = sim.data.qpos.copy()
                target_dof = int(sim.model.jnt_dofadr[sim.model.joint('target_free').id])
                sim.data.qvel[target_dof:target_dof + 6] = np.arange(6) * .01
                object_velocity = sim.data.qvel[target_dof:target_dof + 6].copy()
                with (patch('lastmile_dataflow.scenes.stations.sampling.floor_support', return_value=True),
                      patch('lastmile_dataflow.scenes.stations.sampling.robot_contacts', return_value=[]),
                      patch.object(sim.robot, 'aim_station', side_effect=AssertionError('explicit yaw was aimed'))):
                    rows = sample_stations(sim, {'anchor_world': [1., 0., 1.]}, config,
                                           frozen_stations=candidates[1:], static_only=True)
                    for row, candidate in zip(rows, candidates[1:]):
                        self.assertEqual(row['geometry'], 'valid')
                        self.assertAlmostEqual(row['base'][2], candidate['yaw'])
                        rechecked = initialize_station(sim, row, [1., 0., 1.], config, check_visibility=False)
                        self.assertAlmostEqual(rechecked['base'][2], candidate['yaw'])
                robot_q = set(sim.robot.addresses.values())
                object_q = [i for i in range(sim.model.nq) if i not in robot_q]
                np.testing.assert_array_equal(sim.data.qpos[object_q], source[object_q])
                np.testing.assert_array_equal(sim.data.qvel[target_dof:target_dof + 6], object_velocity)
            finally:
                sim.close()

    def test_invisible_physical_station_is_kept_and_visibility_recorded(self):
        from types import SimpleNamespace
        from lastmile_dataflow.scenes.stations.sampling import initialize_station
        sim = SimpleNamespace(started=False, closed=False, target_id=0,
            data=SimpleNamespace(qpos=np.zeros(1), qvel=np.zeros(1)), model=SimpleNamespace(nq=1, nv=1, jnt_dofadr=[], body=lambda _:SimpleNamespace(name='target')),
            robot=SimpleNamespace(config=SimpleNamespace(initial={'base':[0,0,0]}), initial_base_field='base',
                addresses={}, joints={}, aim_station=lambda *args:{}, group=lambda _:np.zeros(3), observation_camera='head'))
        config = SimpleNamespace(min_target_pixels=24, width=8, height=8, require_target_visibility=False)
        with (patch('lastmile_dataflow.scenes.stations.sampling.robot_contacts',return_value=[]),
              patch('lastmile_dataflow.scenes.stations.sampling.mujoco.mj_forward'),
              patch('lastmile_dataflow.scenes.stations.sampling.floor_support',return_value=True),
              patch('lastmile_dataflow.scenes.stations.sampling.head_frame',
                    return_value=(None,None,{'visible_pixels':0}))):
            row = initialize_station(sim,{'xy':[0,0]},[1,0,1],config)
            self.assertEqual(row['geometry'],'valid')
            self.assertEqual(row['visibility']['visible_pixels'],0)
            config.require_target_visibility = True
            self.assertEqual(initialize_station(sim,{'xy':[0,0]},[1,0,1],config)['geometry'],'visibility_filtered')

    def test_sampling_defaults_are_independent_of_embodiment(self):
        config = load_task_mode_recipe('configs/task_modes/panda_omron_task_modes.json')
        self.assertEqual(config.geometry.radius_m, 1.)
        self.assertEqual(config.geometry.spacing_m, .25)
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
    def test_static_qualification_reuses_prefix_and_checks_all_remaining_candidates(self):
        from types import SimpleNamespace
        from lastmile_dataflow.scenes.stations import sampling

        task = {'anchor_world': [0, 0, 1]}
        config = SimpleNamespace(radius_m=.2, spacing_m=.1, navigation_whole_body=False)
        lattice = disk_samples(task['anchor_world'], config.radius_m, config.spacing_m)
        prefix = [{**s, 'geometry': 'geometry_filtered', 'reason': 'fixture'} for s in lattice[:3]]
        clone = lambda _: SimpleNamespace(close=lambda: None, data=SimpleNamespace(warning=[]))
        with (patch.object(sampling, 'clone_preparation', side_effect=clone),
              patch.object(sampling, 'room_triangles', return_value=np.empty((0, 3, 2))),
              patch.object(sampling, 'initialize_station',
                           side_effect=lambda sim, station, *a, **kw: {**station, 'geometry': 'valid'}) as initialize,
              patch.object(sampling.mujoco, 'Renderer') as renderer):
            rows = sample_stations(None, task, config, static_only=True, checked_stations=prefix)
        self.assertEqual(len(rows), len(lattice))
        self.assertEqual(initialize.call_count, len(lattice) - len(prefix))
        self.assertEqual(rows[:3], prefix)
        renderer.assert_not_called()

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
            patch('lastmile_dataflow.scenes.stations.sampling.room_triangles', return_value=np.empty((0, 3, 2))),
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
            patch('lastmile_dataflow.scenes.stations.sampling.room_triangles', return_value=np.empty((0, 3, 2))),
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
