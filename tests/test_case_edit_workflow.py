from dataclasses import replace
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.construction.case_schema import CaseEditRequest, GenerationConfig, Review
from lastmile_dataflow.construction.layout_identity import layout_identity
from lastmile_dataflow.io import read_json
from lastmile_dataflow.runtime.preparation import prepare_scene
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.workflows.case_edit import run_case_edit
from lastmile_dataflow.workflows.case_edit_supervisor import supervised_case_edit


def crash_worker(request, robot, collection, options):
    from lastmile_dataflow.io import write_json
    from lastmile_dataflow.construction.case_schema import RunResult
    path = Path(collection['output_dir'])/'case_edits'/options['run_id']
    path.mkdir(parents=True)
    write_json(path/'result.json', RunResult(options['run_id'], 'partial', 3).to_dict())
    raise RuntimeError('intentional worker crash after checkpoint')


def hang_worker(*args):
    time.sleep(30)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)
        self.collection = CollectionConfig(output_dir=str(self.root/'outputs'))
        self.request = CaseEditRequest('Move the supported object to a visibly different layout',
            {'scene_id': self.source.scene_id, 'xml_path': self.source.xml_path},
            task_context={'robot_base': [0, 0, 0]}, generation=GenerationConfig(target_scene_count=3,
             max_rounds=1, max_samples=6, samples_per_proposal=6, max_agent_calls=10, timeout_s=30))
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def backend(self, **kwargs):
        self.calls.append(kwargs['role'])
        if kwargs['role'] == 'normalizer':
            return template().to_dict()
        if kwargs['role'] == 'proposer':
            return {'proposals': [proposal().to_dict()]}
        return {'sample_id': kwargs['payload']['sample_id'], 'verdict': 'pass', 'checks': [
            {'item': k, 'status': 'pass', 'reason': 'protocol stub, not real model proof',
             'views': ['before/top', 'after/top']} for k in kwargs['payload']['required_visual_checks']]}

    def test_zero_agent_budget_and_no_overwrite(self):
        request = replace(self.request, generation=replace(self.request.generation, max_agent_calls=0))
        path = run_case_edit(request, self.robot, self.collection, backend=self.backend, run_id='zero')
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['attempted_count'], 0)
        self.assertEqual(self.calls, [])
        with self.assertRaises(FileExistsError):
            run_case_edit(request, self.robot, self.collection, backend=self.backend, run_id='zero')

    def test_failed_sampling_counts_without_review(self):
        def backend(**kwargs):
            self.calls.append(kwargs['role'])
            return template().to_dict() if kwargs['role'] == 'normalizer' else {'proposals': [proposal(10).to_dict()]}
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend)
        result = read_json(path/'result.json')
        self.assertEqual(result['attempted_count'], 4)  # bounded strategy rejection streak
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(self.calls, ['normalizer', 'proposer'])
        self.assertTrue(all(read_json(p)['status'] == 'search_exhausted' for p in (path/'samples').glob('*/sample.json')))

    def test_missing_roles_and_global_call_budget(self):
        def backend(**kwargs):
            self.calls.append(kwargs['role'])
            return template().to_dict() if kwargs['role'] == 'normalizer' else {'proposals': []}
        request = replace(self.request, generation=replace(self.request.generation, max_rounds=5, max_agent_calls=2))
        path = run_case_edit(request, self.robot, self.collection, backend=backend)
        result = read_json(path/'result.json')
        self.assertEqual(result['reason'], 'agent_call_budget_exhausted')
        self.assertEqual(result['attempted_count'], 0)
        self.assertEqual(self.calls, ['normalizer', 'proposer'])

    def test_no_changes_rejected_before_visual_review(self):
        p = proposal().to_dict()
        p['operations'] = [{'op': 'rotate', 'subject': '$target', 'axis': [0, 0, 1], 'angle': {'value': 0}}]
        def backend(**kwargs):
            self.calls.append(kwargs['role'])
            return template().to_dict() if kwargs['role'] == 'normalizer' else {'proposals': [p]}
        path = run_case_edit(self.request, self.robot, self.collection, backend=backend)
        self.assertEqual(read_json(path/'result.json')['accepted_count'], 0)
        self.assertNotIn('reviewer', self.calls)
        self.assertTrue(all(read_json(p)['status'] == 'duplicate_or_unchanged' for p in (path/'samples').glob('*/sample.json')))

    def test_final_layout_identity_ignores_added_names_and_quaternion_sign(self):
        with prepare_scene(self.source, self.robot, base=[0, 0, 0]) as prepared:
            graph = prepared.graph.to_dict()
            originals = set(graph['nodes'])
            node = copy.deepcopy(graph['nodes']['target'])
            node.update(asset_id='box', pose={'position': [4, 0, 1], 'quaternion_wxyz': [1, 0, 0, 0]})
            graph['nodes']['random_added_1'] = node
            other = copy.deepcopy(graph)
            other['nodes']['random_added_2'] = other['nodes'].pop('random_added_1')
            other['nodes']['random_added_2']['pose']['quaternion_wxyz'] = [-1, 0, 0, 0]
            self.assertEqual(layout_identity(graph, originals), layout_identity(other, originals))

    def test_hard_deadline_stops_worker_without_fake_success(self):
        request = replace(self.request, generation=replace(self.request.generation, timeout_s=.4))
        start = time.monotonic()
        path = supervised_case_edit(request, self.robot, self.collection, api_settings=None, _worker=hang_worker)
        self.assertLess(time.monotonic()-start, 5)
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['reason'], 'hard_wall_clock_deadline')
        self.assertEqual(result['accepted_count'], 0)

    def test_worker_crash_does_not_return_partial_as_final(self):
        path = supervised_case_edit(self.request, self.robot, self.collection,
                                    api_settings=None, _worker=crash_worker)
        self.assertEqual(read_json(path/'result.json')['status'], 'infrastructure_error')

    def test_uncertain_skips_binding_and_forwards_visual_feedback(self):
        seen_feedback = []
        def backend(**kwargs):
            self.calls.append(kwargs['role'])
            if kwargs['role'] == 'normalizer':
                return template().to_dict()
            seen_feedback.append(kwargs['payload']['feedback'])
            return {'proposals': [proposal().to_dict()]}
        def uncertain(request, template, proposal, executable, *args, **kwargs):
            return Review.from_dict({'sample_id': executable.sample_id, 'verdict': 'uncertain',
                'checks': [{'item': 'intent', 'status': 'unknown', 'reason': 'hidden before object'}]}), {'pair_id': 'stub'}
        request = replace(self.request, generation=replace(self.request.generation, max_rounds=2, max_agent_calls=3))
        with patch('lastmile_dataflow.workflows.case_edit.review_pending_trial', side_effect=uncertain):
            path = run_case_edit(request, self.robot, self.collection, backend=backend)
        result = read_json(path/'result.json')
        self.assertEqual(result['attempted_count'], 2)
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(self.calls, ['normalizer', 'proposer', 'proposer'])
        self.assertEqual(seen_feedback[1][0]['visual_checks'][0]['reason'], 'hidden before object')

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in actual RGB with MOCK models')
    def test_three_unique_scenes_actual_render_mock_models(self):
        path = run_case_edit(self.request, self.robot, self.collection, backend=self.backend,
                             view_config=ViewConfig(width=320, height=240))
        result = read_json(path/'result.json')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['accepted_count'], 3)
        self.assertEqual(self.calls.count('normalizer'), 1)
        self.assertEqual(result['results']['task_completion'], 'unknown')
        identities = []
        for sample_id in result['accepted_samples']:
            sample_path = path/'samples'/sample_id
            identities.append(read_json(sample_path/'sample.json')['layout_id'])
            self.assertEqual(len(list((sample_path/'rgb').glob('*.png'))), 6)
            self.assertTrue((sample_path/'scene/model.mjb').is_file())
        self.assertEqual(len(set(identities)), 3)
