"""场景往返、标定分母与按需模型数据导出。"""

import unittest
from dataclasses import asdict
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.data.evaluation.selection import (
    eligible_stations,
    generate_pairs,
    station_calibration,
)
from lastmile_dataflow.data.evaluation.statistics import aggregate
from lastmile_dataflow.data.export.molmobot_dataset import export_molmobot
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.dataset import load_scenes
from tests.helpers import DatasetTestCase


class DatasetTests(DatasetTestCase):
    def test_scene_roundtrip_and_xml_payload(self):
        dataset = self.dataset()
        record = load_scenes(dataset)[0]
        restored = Simulation.from_snapshot(record["scene_path"], self.robot, target="target")
        try:
            np.testing.assert_array_equal(restored.state_vector(), self.sim.state_vector())
            model = mujoco.MjModel.from_xml_path(str(Path(record["scene_path"]) / "model.xml"))
            self.assertEqual(model.nq, self.sim.model.nq)
            self.assertEqual(
                {p.name for p in Path(record["scene_path"]).iterdir()},
                {"config.json", "model.xml", "model.mjb", "initial.npz"},
            )
        finally:
            restored.close()

    def test_rates_exclude_errors_and_require_complete_points(self):
        config = ExecutionConfig(trials_per_station=2, start_count=1)
        trials = [
            {"station_id": s, "trial": i, "status": "failure" if s == "s0" else "success"}
            for s in ("s0", "s1")
            for i in range(2)
        ]
        binding = {"scene_id": "one", "task_id": "pick-target"}
        rates = station_calibration(self.points, trials, 2, binding, config)
        self.assertEqual(len(generate_pairs(*eligible_stations(self.points, rates, config))), 1)
        incomplete = station_calibration(
            self.points,
            trials[:-1] + [{"station_id": "s1", "trial": 1, "status": "simulation_failure"}],
            2,
            binding,
            config,
        )
        self.assertFalse(incomplete["complete"])
        self.assertEqual(generate_pairs(*eligible_stations(self.points, incomplete, config)), [])

    def test_model_export_keeps_source_and_requires_motor_calibration(self):
        recorder = AttemptRecorder(
            self.root, {"collection": asdict(self.collection), "task": self.task}, attempt_id="r0"
        )
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        self.sim.step(self.sim.robot.neutral_action())
        recorder.finish("failure", "fixture")
        with self.assertRaisesRegex(ValueError, "motor map"):
            export_molmobot(recorder.path, self.root / "bad")
        output = export_molmobot(recorder.path, self.root / "state", state_images_only=True)
        with np.load(output / "data.npz") as data:
            self.assertNotIn("actions", data.files)
            self.assertEqual(data["state"].shape, (2, 22))
        self.assertTrue((recorder.path / "trajectory.npz").is_file())


def station(name, xy, geometry="valid"):
    return {"station_id": name, "xy": xy, "geometry": geometry}


class StatisticsTests(unittest.TestCase):
    def test_unknown_and_filtered_are_not_failure(self):
        stations = [station("a", [0, 0]), station("b", [1, 0], "geometry_filtered")]
        trials = [
            {"station_id": "a", "status": "success", "attempt": "1"},
            {"station_id": "a", "status": "infrastructure_error", "attempt": "2"},
        ]
        rows = aggregate(stations, trials, 5)
        self.assertEqual(rows[0]["success_rate"], 1)
        self.assertIsNone(rows[1]["success_rate"])
        self.assertFalse(rows[0]["complete"])
