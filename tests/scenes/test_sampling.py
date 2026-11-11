"""站位采样、初始化过滤与覆盖压缩。"""

import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.runtime.control.joints import JointMap
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.stations.sampling import coverage_select
from lastmile_dataflow.scenes.stations.sampling import disk_samples
from lastmile_dataflow.scenes.dataset_config import load_scene_recipe


class SamplingTests(unittest.TestCase):
    def test_explicit_yaw_survives_initialization_and_recheck(self):
        import tempfile
        from lastmile_dataflow.scenes.stations.sampling import initialize_station
        from tests.helpers import make_sim

        with tempfile.TemporaryDirectory() as directory:
            sim, _ = make_sim(directory)
            try:
                nominal = {**disk_samples([0, 0, 1], 1., .25)[0],
                           'base': [0., -1., 0.], 'initial': sim.robot.config.initial}
                candidates = [{**nominal, 'station_id': f"{nominal['xy_id']}-Y{i}", 'yaw': yaw, 'yaw_source': 'explicit'}
                              for i, yaw in enumerate((-np.pi / 4, np.pi / 4), 1)]
                source = sim.data.qpos.copy()
                target_dof = int(sim.model.jnt_dofadr[sim.model.joint('target_free').id])
                sim.data.qvel[target_dof:target_dof + 6] = np.arange(6) * .01
                object_velocity = sim.data.qvel[target_dof:target_dof + 6].copy()
                with (patch('lastmile_dataflow.scenes.stations.sampling.floor_support', return_value=True),
                      patch('lastmile_dataflow.scenes.stations.sampling.robot_contacts', return_value=[]),
                      patch.object(sim.robot, 'aim_station', side_effect=AssertionError('explicit yaw was aimed'))):
                    for candidate in candidates:
                        row = initialize_station(sim, candidate, [1., 0., 1.])
                        self.assertEqual(row['geometry'], 'valid')
                        self.assertAlmostEqual(row['base'][2], candidate['yaw'])
                        rechecked = initialize_station(sim, row, [1., 0., 1.])
                        self.assertAlmostEqual(rechecked['base'][2], candidate['yaw'])
                robot_q = set(sim.robot.joints.addresses.values())
                object_q = [i for i in range(sim.model.nq) if i not in robot_q]
                np.testing.assert_array_equal(sim.data.qpos[object_q], source[object_q])
                np.testing.assert_array_equal(sim.data.qvel[target_dof:target_dof + 6], object_velocity)
            finally:
                sim.close()

    def test_physical_station_check_does_not_render(self):
        from types import SimpleNamespace
        from lastmile_dataflow.scenes.stations.sampling import initialize_station
        sim = SimpleNamespace(started=False, closed=False, target_id=0,
            data=SimpleNamespace(qpos=np.zeros(1), qvel=np.zeros(1)), model=SimpleNamespace(nq=1, nv=1, jnt_dofadr=[], body=lambda _:SimpleNamespace(name='target')),
            robot=SimpleNamespace(config=SimpleNamespace(initial={'base':[0,0,0]}), initial_base_field='base',
                joints=JointMap({}, {}, {}, {}, {}, {}), aim_station=lambda *args:{}, group=lambda _:np.zeros(3), observation_camera='head'))
        with (patch('lastmile_dataflow.scenes.stations.sampling.robot_contacts',return_value=[]),
              patch('lastmile_dataflow.scenes.stations.sampling.mujoco.mj_forward'),
              patch('lastmile_dataflow.scenes.stations.sampling.floor_support',return_value=True)):
            row = initialize_station(sim,{'xy':[0,0]},[1,0,1])
            self.assertEqual(row['geometry'],'valid')
            self.assertIsNone(row['visibility'])

    def test_sampling_defaults_are_independent_of_embodiment(self):
        config = load_scene_recipe('configs/scenes/panda_omron.json')
        self.assertEqual(config.geometry.radius_m, 1.)
        self.assertEqual(config.geometry.spacing_m, .25)
        self.assertEqual(config.max_final_stations, 20)

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

