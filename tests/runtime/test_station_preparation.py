"""Shared preparation advances once; independent trials do not repeat settling."""
from itertools import count
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.runtime.execution.context import clone_sim
from lastmile_dataflow.runtime.execution.preparation import prepare_station
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.utils.io import read_json
from lastmile_dataflow.workflows.collection import _station_records
from tests.helpers import DatasetTestCase


class StationPreparationTests(DatasetTestCase):
    def test_station_settles_once_and_three_trials_receive_independent_measured_state(self):
        config = ExecutionConfig(initial_settle_s=.1, shared_station_preparation=True, trials_per_station=3)
        initial = self.sim.state_vector().copy()
        states = []

        def execute(baseline, scene, station, *args, prepared, **kwargs):
            with self.subTest(trial=len(states)):
                sim = clone_sim(baseline, target='target')
                self.addCleanup(sim.close)
                prepared.state.initialize(sim)
                states.append(sim.state_vector().copy())
                self.assertFalse(sim.started)
                self.assertAlmostEqual(sim.data.time, .1)
                sim.begin()
                sim.step(sim.robot.neutral_action())
            return {'status': 'failure', 'reason': 'fixture', 'executed_steps': 1}

        with (patch('lastmile_dataflow.runtime.execution.preparation.prepare_station', wraps=prepare_station) as prepare,
              patch('lastmile_dataflow.workflows.collection._execute', side_effect=execute)):
            records = list(_station_records(self.sim, self.scene, 0, 3, self.root, config,
                                           self.collection, self.root, attempt_ids=count()))
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual([r['seed'] for r in records], [42, 43, 44])
        for state in states[1:]:
            np.testing.assert_array_equal(state, states[0])
        np.testing.assert_array_equal(self.sim.state_vector(), initial)
        metadata = read_json(self.root / 'preparations/one/s0/preparation.json')
        self.assertEqual(metadata['status'], 'ready')
        self.assertAlmostEqual(metadata['settled_simulation_s'], .1)

    def test_preparation_failure_is_not_copied_into_three_trial_failures(self):
        config = ExecutionConfig(shared_station_preparation=True, trials_per_station=3)
        with (patch('lastmile_dataflow.runtime.execution.preparation.floor_support', return_value=False),
              patch('lastmile_dataflow.workflows.collection._execute') as execute):
            records = list(_station_records(self.sim, self.scene, 0, 3, self.root, config,
                                           self.collection, self.root, attempt_ids=count()))
        self.assertEqual(records, [])
        execute.assert_not_called()
        result = read_json(self.root / 'preparations/one/s0/preparation.json')
        self.assertEqual(result['status'], 'initialization_error')
