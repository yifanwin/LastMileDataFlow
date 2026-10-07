"""Single-scene construction regression. Mock models are NEVER task evidence."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import mujoco

from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.construction.scene_request import (SceneInput, SceneConstructionRequest,
    LocalConstructionConfig, ConstructionBudget, MobileCaseTemplate)
from lastmile_dataflow.io import read_json, file_digest
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.runtime.preparation import SettleConfig
from lastmile_dataflow.workflows.case_edit import run_case_edit
from lastmile_dataflow.workflows.case_edit_supervisor import supervised_case_edit


def hang_worker(*args):
    time.sleep(30)


def crash_worker(request, robot, collection, options):
    from lastmile_dataflow.io import write_json
    from lastmile_dataflow.workflows.case_edit import initial_result
    from lastmile_dataflow.construction.scene_request import SceneConstructionRequest
    path = Path(collection['output_dir'])/'case_construction'/options['run_id']
    path.mkdir(parents=True)
    write_json(path/'result.json', initial_result(SceneConstructionRequest.from_dict(request), options['run_id']))
    raise RuntimeError('intentional crash after checkpoint')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)
        scene = Path(self.source.xml_path)
        scene.write_text(scene.read_text().replace('type="plane" size="0 0 .01"', 'type="box" pos="0 0 -.01" size="5 5 .01"'))
        self.collection = CollectionConfig(output_dir=str(self.root/'outputs'))
        self.request = SceneConstructionRequest('Construct geometry-induced repositioning',
            SceneInput('single', scene_id=self.source.scene_id, xml_path=self.source.xml_path), 'case1',
            construction=LocalConstructionConfig(min_target_pixels=8),
            budgets=ConstructionBudget(max_rounds=1, max_samples=4, samples_per_proposal=4, timeout_s=40))
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def backend(self, **kwargs):
        self.calls.append(kwargs['role'])
        payload = kwargs['payload']
        if kwargs['role'] == 'normalizer':
            return {**template().to_dict(), 'case_type': payload['case_type'], 'task_type': 'pick',
                'objective_mode': payload['objective_mode'], 'task_hypotheses': ['Actual mobile pick remains untested'], 'assumptions': []}
        if kwargs['role'] == 'strategist':
            c = next(c for c in payload['contexts'] if c['target'] == 'target')
            return {'decision': 'select', 'context_id': c['context_id'], 'radius_m': 2.5,
                'edit_directions': ['move'], 'expected_layout': 'protocol fixture only',
                'contrast_spec': [{'work_region_id': region['region_id'], 'role': role, 'expected_mechanism': 'mock hypothesis only'}
                    for region, role in zip(c['work_regions'], ('base_space_constrained','arm_unfavorable','preferred'))],
                'asset_requests': [], 'rationale': 'mock selection, not suitability proof', 'unverified_claims': []}
        if kwargs['role'] == 'proposer':
            self.assertGreaterEqual(len(kwargs['images']), 1)
            self.assertIn('station_start', payload['context']['graph']['nodes'])
            return {'decision': 'propose', 'information_request': None, 'proposals': [proposal(-.20).to_dict()]}
        self.assertGreaterEqual(len(kwargs['images']), 2)
        return {'checks': {key: {'status': 'pass', 'reason': 'mock protocol fixture, not real semantic proof',
             'views': ['before/head', 'after/head']} for key in payload['required_visual_checks']},
             'information_request': None, 'agent_assessment': {'conclusion': 'unknown',
                'reason': 'robot task is not tested', 'preferred_regions': []}}

    def test_zero_agent_budget_no_overwrite(self):
        request = replace(self.request, budgets=replace(self.request.budgets, max_agent_calls=0))
        path = run_case_edit(request, self.robot, self.collection, backend=self.backend, run_id='zero')
        self.assertEqual(read_json(path/'result.json')['status'], 'budget_exhausted')
        self.assertEqual(self.calls, [])
        with self.assertRaises(FileExistsError):
            run_case_edit(request, self.robot, self.collection, backend=self.backend, run_id='zero')

    def test_dataset_and_mobile_mode_explicitly_unsupported(self):
        for request in (replace(self.request, scene_input=SceneInput('dataset', dataset_dir=str(self.root))),
                        replace(self.request, verification_mode='mobile_task')):
            path = run_case_edit(request, self.robot, self.collection, backend=self.backend)
            result = read_json(path/'result.json')
            self.assertEqual(result['status'], 'unsupported')
            self.assertFalse(result['version_delivery_complete'])
            self.assertEqual(result['case_verified_count'], 0)
            self.assertEqual(result['results']['task_completion'], 'unknown')
        self.assertEqual(self.calls, [])

    def test_no_scene_retrieval_for_explicit_scene(self):
        request = replace(self.request, budgets=replace(self.request.budgets, max_agent_calls=0))
        with patch('lastmile_dataflow.catalog.index.search_index', side_effect=AssertionError('must not retrieve houses')):
            path = run_case_edit(request, self.robot, self.collection, backend=self.backend)
        self.assertEqual(read_json(path/'source.json')['scene_id'], self.source.scene_id)

    def test_template_does_not_change_requested_case(self):
        def backend(**kwargs):
            return {**template().to_dict(), 'case_type': 'case3', 'task_hypotheses': ['untested']}
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend)
        self.assertEqual(read_json(path/'result.json')['status'], 'failed')

    def test_hard_deadline_and_worker_crash(self):
        request = replace(self.request, budgets=replace(self.request.budgets, timeout_s=.3))
        start = time.monotonic()
        path = supervised_case_edit(request, self.robot, self.collection, api_settings=None, _worker=hang_worker)
        self.assertLess(time.monotonic()-start, 5)
        self.assertEqual(read_json(path/'result.json')['reason'], 'hard_wall_clock_deadline')
        path = supervised_case_edit(self.request, self.robot, self.collection, api_settings=None, _worker=crash_worker)
        self.assertEqual(read_json(path/'result.json')['status'], 'infrastructure_error')

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in real rendering / MOCK models')
    def test_unedited_source_velocity_not_a_construction_gate(self):
        original = mujoco.mj_objectVelocity

        def unstable_original(model, data, kind, body, velocity, local):
            original(model, data, kind, body, velocity, local)
            if model.body(body).name == 'neighbor':
                velocity[3] = 100.

        with patch('mujoco.mj_objectVelocity', side_effect=unstable_original):
            path = run_case_edit(self.request, self.robot, self.collection, backend=self.backend,
                settle_config=SettleConfig(require_source_stability=False), view_config=ViewConfig(width=320, height=240))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'completed', result)
        sample = path/'samples'/result['accepted_samples'][0]
        settling = read_json(sample/'rule_checks.json')['settling']
        self.assertTrue(settling['stable'])
        self.assertFalse(settling['observed_scene_stable'])
        self.assertEqual(settling['stability_scope'], ['target'])
        self.assertEqual(read_json(sample/'graph_after.json')['stage'], 'observed')

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in real rendering / MOCK models')
    def test_three_case_families_head_pair_and_unknown_task(self):
        source_hash = file_digest(self.source.xml_path)
        for case_type in ('case1', 'case1.5', 'case3'):
            self.calls.clear()
            request = replace(self.request, case_type=case_type)
            path = run_case_edit(request, self.robot, self.collection, backend=self.backend,
                                 view_config=ViewConfig(width=320, height=240))
            result = read_json(path/'result.json')
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual(self.calls, ['normalizer', 'strategist', 'proposer', 'reviewer'])
            self.assertEqual(result['results']['case_condition'], 'unknown')
            self.assertEqual(result['results']['task_completion'], 'unknown')
            self.assertFalse(result['version_delivery_complete'])
            sample = path/'samples'/result['accepted_samples'][0]
            pair = read_json(sample/'rgb/pair.json')
            self.assertTrue(pair['valid'])
            self.assertEqual(file_digest(sample/'rgb/before_head.png'), pair['before_image_sha256'])
            self.assertTrue((sample/'scene/model.mjb').is_file())
            self.assertEqual(read_json(sample/'task_validation/status.json')['status'], 'not_tested')
            initialization = read_json(path/'contexts/round_000/initialization/initialization.json')
            self.assertEqual(initialization['valid_candidates'], 3)
            self.assertEqual(initialization['navigation_reachability'], 'unknown')
            self.assertGreater(len(pair['images']), 2)
        self.assertEqual(file_digest(self.source.xml_path), source_hash)

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'actual EGL / mock Agent')
    def test_format_repair_does_not_reedit_and_advice_does_not_gate(self):
        calls, images = [], []
        def backend(**kwargs):
            response = self.backend(**kwargs)
            if kwargs['role'] == 'reviewer':
                calls.append(kwargs['payload'])
                images.append(kwargs['images'])
                response['agent_assessment']['conclusion'] = 'unlikely_beneficial'
                if len(calls) == 1:
                    response['intent'] = 'invalid extra field'
            return response
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend,
                             view_config=ViewConfig(width=320, height=240))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['attempted_count'], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(images[0], images[1])
        self.assertIn('previous_response', calls[1])
        self.assertEqual(result['case_verified_count'], 0)

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'actual EGL / mock Agent')
    def test_exhausted_format_repair_stops_without_sampling_new_layout(self):
        def backend(**kwargs):
            response = self.backend(**kwargs)
            if kwargs['role'] == 'reviewer':
                response['intent'] = 'always invalid'
            return response
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend,
                             view_config=ViewConfig(width=320, height=240, initial_aux_views=0))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['attempted_count'], 1)
        self.assertEqual(self.calls.count('reviewer'), 5)
        self.assertEqual(read_json(path/'samples/sample_000000/sample.json')['status'], 'agent_protocol_error')

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'actual EGL / mock Agent')
    def test_proposer_information_request_executes_in_same_round(self):
        count = 0
        def backend(**kwargs):
            nonlocal count
            response = self.backend(**kwargs)
            if kwargs['role'] == 'proposer':
                count += 1
                if count == 1:
                    return {'decision': 'request_information', 'proposals': [], 'information_request': {
                        'kind': 'aux_view', 'reason': 'need actual tabletop image', 'node_ids': ['target'], 'view_hint': 'top'}}
                self.assertGreater(len(kwargs['images']), 1)
            return response
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend,
            view_config=ViewConfig(width=320, height=240, initial_aux_views=0))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(count, 2)
        self.assertEqual(result['attempted_count'], 1)
        resolutions = [f['resolution'] for f in read_json(path/'progress.json')['feedback'] if f.get('stage') == 'proposal_information']
        self.assertEqual(resolutions[0]['status'], 'fulfilled')

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'actual EGL / mock Agent')
    def test_reviewer_requests_fixed_pairs_without_new_edit(self):
        count = 0
        def backend(**kwargs):
            nonlocal count
            response = self.backend(**kwargs)
            if kwargs['role'] == 'reviewer':
                count += 1
                if count == 1:
                    for check in response['checks'].values():
                        check['status'] = 'unknown'
                    response['information_request'] = {'kind': 'aux_view', 'reason': 'need paired target view',
                                                        'node_ids': ['target'], 'view_hint': 'top'}
                else:
                    refs = {i['view'] for i in kwargs['images']}
                    self.assertIn('before/aux_000', refs)
                    self.assertIn('after/aux_000', refs)
            return response
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend,
            view_config=ViewConfig(width=320, height=240, initial_aux_views=0))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['attempted_count'], 1)
        self.assertEqual(count, 2)
        pair = read_json(path/'samples/sample_000000/rgb/pair.json')
        self.assertEqual({i['pair_id'] for i in pair['images']}, {pair['pair_id']})
