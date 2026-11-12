"""场景往返、标定分母与按需模型数据导出。"""

import itertools
import unittest
from dataclasses import asdict
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.data.evaluation.calibration import (
    aggregate,
    eligible_stations,
    station_calibration,
)
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.dataset import load_scenes
from lastmile_dataflow.utils.io import append_row
from tests.helpers import DatasetTestCase


class DatasetTests(DatasetTestCase):
    def test_scene_selection_ignores_nonmaterialized_task_mode_records(self):
        dataset = self.dataset()
        accepted = load_scenes(dataset)[0]
        append_row(
            dataset / "scenes.jsonl",
            {"status": "skipped", "proposal_intent": "articulated_interaction"},
        )
        append_row(
            dataset / "scenes.jsonl",
            {"status": "rejected", "reason": "task_preparation_failed"},
        )
        self.assertEqual(load_scenes(dataset, [accepted["scene_id"]]), [accepted])

    def test_scene_roundtrip_and_xml_payload(self):
        dataset = self.dataset()
        record = load_scenes(dataset)[0]
        restored = Simulation.from_snapshot(
            record["scene_path"], self.robot, target="target"
        )
        try:
            np.testing.assert_array_equal(
                restored.state_vector(), self.sim.state_vector()
            )
            model = mujoco.MjModel.from_xml_path(
                str(Path(record["scene_path"]) / "model.xml")
            )
            self.assertEqual(model.nq, self.sim.model.nq)
            self.assertEqual(
                {p.name for p in Path(record["scene_path"]).iterdir()},
                {"config.json", "model.xml", "model.mjb", "initial.npz"},
            )
        finally:
            restored.close()

    def test_rates_exclude_errors_and_require_complete_points(self):
        config = ExecutionConfig(trials_per_station=2)
        trials = [
            {
                "station_id": s,
                "trial": i,
                "status": "failure" if s == "s0" else "success",
            }
            for s in ("s0", "s1")
            for i in range(2)
        ]
        binding = {"scene_id": "one", "task_id": "pick-target"}
        rates = station_calibration(self.points, trials, 2, binding, "curobo")
        self.assertEqual(
            len(list(itertools.product(*eligible_stations(self.points, rates, config.lastmile_low, config.lastmile_high)))), 1
        )
        incomplete = station_calibration(
            self.points,
            trials[:-1]
            + [{"station_id": "s1", "trial": 1, "status": "simulation_failure"}],
            2,
            binding,
            "curobo",
        )
        self.assertFalse(incomplete["complete"])
        self.assertEqual(
            eligible_stations(self.points, incomplete, config.lastmile_low, config.lastmile_high), ([], [])
        )


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
