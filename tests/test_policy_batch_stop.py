"""A real shared RPC failure must not be followed by another trial on that stream."""
from itertools import count
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lastmile_dataflow.io import rows
from lastmile_dataflow.workflows.collection import _station_trials


class PolicyBatchStopTests(unittest.TestCase):
    def test_shared_failure_preserves_one_unknown_trial_and_stops(self):
        scene = {'scene_id': 'one', 'task': {'task_id': 'pick'},
                 'stations': [{'station_id': 'S0'}, {'station_id': 'S1'}]}
        config = SimpleNamespace(seed=17, trials_per_station=2)
        result = {'status': 'infrastructure_error', 'failure_scope': 'shared',
                  'reason': 'model response timeout', 'executed_steps': 0}
        with tempfile.TemporaryDirectory() as tmp, patch(
                'lastmile_dataflow.workflows.collection._execute', return_value=result) as execute:
            trials = _station_trials(None, scene, None, config, None, Path(tmp), attempt_ids=count())
            self.assertEqual(len(trials), 1)
            self.assertEqual(len(rows(Path(tmp) / 'trials.jsonl')), 1)
            execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
