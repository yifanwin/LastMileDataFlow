"""Exact saved planning states and evidence bindings; rendering is a numerical fixture."""
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import mujoco

from lastmile_dataflow.data.evaluation.diagnosis_inputs import PreparedMeasurements, planning_evidence
from lastmile_dataflow.planning.navigation.astar import OccupancyMap
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.stations.sampling import place_frozen_station
from lastmile_dataflow.utils.io import write_json
from tests.helpers import DatasetTestCase


class VisibilityFixture:
    def __init__(self, *args):
        pass
    def measure(self, sim):
        return {'complete': True, 'value': .5, 'known_max': .5}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class DiagnosisInputTests(DatasetTestCase):
    def test_measurement_and_path_use_saved_actual_state_without_mutating_baseline(self):
        target = self.sim.model.body('target').id
        joint = int(self.sim.model.body_jntadr[target])
        address = int(self.sim.model.jnt_qposadr[joint])
        self.sim.data.qpos[address + 1] = .7
        self.sim.data.qpos[address + 3:address + 7] = [np.sqrt(.5), np.sqrt(.5), 0., 0.]
        mujoco.mj_forward(self.sim.model, self.sim.data)
        planning = {}
        trials = []
        before = self.sim.state_vector().copy()
        for index, station in enumerate(self.points):
            from lastmile_dataflow.runtime.execution.context import clone_sim
            probe = clone_sim(self.sim, target='target')
            place_frozen_station(probe, station)
            reference = self.root / f'state-{index}.npz'
            probe.save_snapshot(reference)
            probe.close()
            trial = {'station_id': station['station_id'], 'rollout_id': f'r{index}'}
            trials.append(trial)
            planning[trial['rollout_id']] = {'attempts': [{'planning_state_ref': str(reference)}]}
        self.points[1]['xy'] = [9., 9.]  # Requested metadata must not replace the saved actual base.
        prepared = PreparedMeasurements(self.sim, self.scene, trials, planning, ExecutionConfig())
        occupancy = OccupancyMap(np.array([-5., -5.]), .1, np.zeros((101, 101), bool), np.full((101, 101), 5.), ())
        with (patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.TaskVisibility', VisibilityFixture),
              patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.scene_occupancy', return_value=occupancy),
              patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.floor_support', return_value=True)):
            measured = prepared.measure()
        self.assertEqual(measured['s0']['base'][:2], [0., 0.])
        np.testing.assert_allclose(measured['s1']['base'][:2], [.4, 0.])
        for row in measured.values():
            self.assertAlmostEqual(np.linalg.norm(row['base_in_target_frame']), row['target_distance_m'])
        path = prepared.path(self.points[0], self.points[1])
        self.assertTrue(path['segment_free'])
        self.assertAlmostEqual(path['path_length_m'], .4)
        np.testing.assert_array_equal(self.sim.state_vector(), before)
        self.assertFalse(self.sim.started)

    def test_missing_planning_state_remains_unresolved(self):
        prepared = PreparedMeasurements(self.sim, self.scene, [], {}, ExecutionConfig())
        with patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.TaskVisibility', VisibilityFixture):
            measured = prepared.measure()
        self.assertEqual(measured['s0']['status'], 'unresolved')
        self.assertEqual(prepared.path(self.points[0], self.points[1])['status'], 'unresolved')

    def evidence(self):
        trial = {'rollout_id': 'r0', 'scene_id': 'one', 'trial': 0, 'seed': 42, 'planning_ref': 'planning.json'}
        inputs = self.root / 'inputs.json'
        write_json(inputs, {'seeds': [43, 42]})
        attempt = {'phase_evidence': [{'phase': 'goalset'}], 'batch_slot': 1,
                   'grasp_inputs_ref': str(inputs), 'identity': {'attempt_id': 'r0', 'scene_id': 'one', 'trial': 0, 'seed': 42}}
        write_json(self.root / 'planning.json', [attempt, {'diagnostics': attempt}])
        return trial

    def test_duplicate_native_summary_does_not_duplicate_attempt_evidence(self):
        trial = self.evidence()
        observed = planning_evidence(self.root, [trial])['r0']
        self.assertEqual(observed['status'], 'known')
        self.assertEqual(len(observed['attempts']), 1)
        self.assertEqual(observed['attempts'][0]['batch_slot'], 1)

    def test_wrong_lane_identity_is_not_reassigned_to_the_trial(self):
        trial = self.evidence()
        trial['seed'] = 43
        with self.assertRaisesRegex(ValueError, 'binding mismatch'):
            planning_evidence(self.root, [trial])

    def test_old_unmapped_trace_order_does_not_create_phase_evidence(self):
        trial = self.evidence()
        write_json(self.root / 'planning.json', [{'queries': [{'success': True}], 'solver_trace': [{'success': False}]}])
        self.assertEqual(planning_evidence(self.root, [trial])['r0']['status'], 'unresolved')
