"""Parent diagnosis output, one probe per S0 and cancellation of an expired owned worker."""
from dataclasses import asdict
from pathlib import Path
import time
from unittest.mock import patch

from lastmile_dataflow.runtime.execution.protocol import PlanningResponse, SimulationState
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.mode_config import ModeConfig
from lastmile_dataflow.workflows.station_calibration import diagnose_stations
from lastmile_dataflow.utils.io import rows, write_json, write_rows
from tests.helpers import DatasetTestCase


class ProbeFixtureService:
    def execute(self, request):
        root = Path(request.inputs['root']) / 'planning' / request.query_id
        root.mkdir(parents=True)
        write_json(root / 'query.json', {'started_at': time.monotonic(), 'identity': asdict(request.identities[0])})
        probe = {'status': 'measured', 'feasible_ratio': 0., 'feasible_count': 0, 'observed_evaluation_count': 32,
                 'query_ref': str(root / 'query.json')}
        write_json(root / 'ik_probe.json', probe)
        return [PlanningResponse(request.query_id, request.identities[0], 'success', .01, 0, {'probe': probe})]
    def close(self):
        pass


class ExpiredProbeFixtureService(ProbeFixtureService):
    def execute(self, request):
        root = Path(request.inputs['root']) / 'planning' / request.query_id
        root.mkdir(parents=True)
        write_json(root / 'query.json', {'started_at': time.monotonic() - 61.})
        time.sleep(10.)  # Parent must terminate this owned process, without waiting for this call.
        raise AssertionError('expired native call should have been terminated')


class MeasurementFixture:
    def __init__(self, baseline, scene, trials, planning, config, **kwargs):
        self.states = {'s0': SimulationState.capture(baseline)}
    def measure(self):
        return {identity: {'status': 'measured', 'target_distance_m': distance,
            'base_in_target_frame': [distance, 0., 0.], 'clearance_base_m': clearance,
            'visibility': {'complete': True, 'value': .8, 'known_max': .8},
            'non_base_initial': {}, 'yaw': 0., 'initialization_valid': True}
            for identity, distance, clearance in [('s0', 1., .04), ('s1', .5, .3)]}
    def path(self, start, goal):
        return {'status': 'measured', 'segment_free': True, 'path_exists': True, 'path_length_m': .5}


class StationDiagnosisTests(DatasetTestCase):
    def setUp(self):
        super().setUp()
        dataset = self.dataset()
        self.scene.update(scene_path=str(dataset / self.scene['scene']), robot_config=self.robot,
                          proposal_intent='reachability', station_hints=['s0'], dataset_split='train')
        for station in self.points:
            station['xy_id'] = station['station_id']
        self.raw = [{ 'scene_id': 'one', 'task_id': 'pick-target', 'station_id': station['station_id'],
                      'trial': trial, 'seed': 42 + trial, 'rollout_id': f'{station["station_id"]}-{trial}',
                      'recording_complete': True, 'planning_ref': 'planning.json',
                      'status': 'failure' if index == 0 else 'success',
                      'task_outcome': {'status': 'failure' if index == 0 else 'success'}}
                    for index, station in enumerate(self.points) for trial in range(3)]
        self.output = self.root / 'diagnosis'
        self.output.mkdir()
        write_json(self.output / 'config.json', {'mode_settings': asdict(ModeConfig()),
                                                'mode_visibility': {'min_reference_pixels': 24}})
        write_rows(self.output / 'trials.jsonl', self.raw)
        self.config = ExecutionConfig(trials_per_station=3, mode_config='configs/task_modes/mode_defaults.json')
        inputs = self.output / 'inputs/grasp_inputs.json'
        write_json(inputs, {'seeds': [42]})
        write_json(inputs.with_name('grasp_candidates.json'), {'lanes': [{'seed': 42, 'candidates': [{'row': 0}]}]})
        self.planning = {r['rollout_id']: {'attempts': [{'grasp_inputs_ref': str(inputs), 'batch_slot': 0,
            'phase_evidence': [{'phase': 'goalset', 'status': 'failure', 'failure_component': 'ik',
                                'input_eligible': True, 'ik_found': False}]}]} for r in self.raw}

    def diagnose(self, service, allow=True):
        before = (self.output / 'trials.jsonl').read_bytes()
        with (patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.PreparedMeasurements', MeasurementFixture),
              patch('lastmile_dataflow.data.evaluation.diagnosis_inputs.planning_evidence', return_value=self.planning)):
            result = diagnose_stations([self.scene], self.raw, 'assets', self.config, self.output, [6],
                                       allow_probes=allow, service_type=service)
        self.assertEqual((self.output / 'trials.jsonl').read_bytes(), before)
        self.assertEqual(len(rows(self.output / 'diagnosis.jsonl')), 1)
        return result

    def test_one_probe_for_s0_and_parent_writes_one_result_without_copying_trials(self):
        result, summary = self.diagnose(ProbeFixtureService)
        self.assertEqual(summary['probe_count'], 1)
        self.assertTrue(result[0]['intent_hit'])
        self.assertEqual(len(result[0]['s0_results']), 1)
        self.assertEqual(set(result[0]['s0_results'][0]['final_labels']), {'reachability', 'narrow_space'})
        queries = list((self.output / 'planning').iterdir())
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0].name.startswith('probe-'))

    def test_active_timeout_terminates_owned_worker_and_keeps_known_other_label(self):
        started = time.monotonic()
        result, summary = self.diagnose(ExpiredProbeFixtureService)
        self.assertLess(time.monotonic() - started, 5.)
        self.assertIsNone(summary['failure'])
        self.assertEqual(summary['probe_count'], 1)
        self.assertEqual(result[0]['probes']['s0']['reason'], 'diagnostic_ik_active_budget')
        labels = result[0]['s0_results'][0]['labels']
        self.assertEqual(labels['reachability']['status'], 'unresolved')
        self.assertEqual(labels['narrow_space']['status'], 'matched')
        self.assertIsNone(result[0]['intent_hit'])

    def test_incomplete_never_measures_or_starts_a_probe(self):
        self.raw.pop()
        with patch('lastmile_dataflow.workflows.station_calibration.PlanningPool') as pool:
            result, summary = self.diagnose(ProbeFixtureService)
        pool.assert_not_called()
        self.assertEqual(summary['probe_count'], 0)
        self.assertFalse(result[0]['calibration_complete'])
        self.assertIsNone(result[0]['intent_hit'])
