"""Wire contracts, charged capability negotiation and evidence responsibilities."""
import copy
from dataclasses import fields
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from case_edit_helpers import template
from lastmile_dataflow.agents.case_gateway import AgentBudget, AgentFormatError, AgentServiceError, CaseGateway, HTTPCaseBackend
from lastmile_dataflow.agents.contracts import InformationRequest, condition_schema, dsl_schema, output_schema, validate_output
from lastmile_dataflow.agents.case_normalizer import normalize_case
from lastmile_dataflow.construction.case_schema import ContractError
from lastmile_dataflow.construction.scene_request import SceneConstructionRequest, SceneInput
from lastmile_dataflow.construction.scene_request import MobileCaseTemplate
from lastmile_dataflow.agents.construction_strategy import ConstructionPlan


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'case_type': 'case1', 'task_type': 'pick', 'objective_mode': 'beneficial_reposition'}
        self.value = {**template().to_dict(), **self.payload, 'task_hypotheses': ['mobile benefit untested'], 'assumptions': []}

    def test_schema_used_for_error_location_and_previous_response(self):
        seen = []
        def backend(**kwargs):
            seen.append(kwargs['payload'])
            return self.value if len(seen) > 1 else {**self.value, 'protected_roles': []}
        gateway = CaseGateway(backend, AgentBudget(3, time.monotonic()+10))
        result = gateway.call('normalizer', 'JSON', self.payload, lambda value: value)
        self.assertEqual(result, self.value)
        self.assertEqual(seen[1]['previous_response']['protected_roles'], [])
        self.assertEqual(seen[1]['format_feedback']['category'], 'agent_protocol_error')
        self.assertEqual(seen[0]['output_contract']['schema'], output_schema('normalizer', self.payload))

    def test_missing_checks_and_fabricated_task_pass_are_rejected(self):
        payload = {'review_scope': 'visible_construction_only', 'required_visual_checks': {'layout:0': 'visible layout'},
                   'view_registry': [{'view': 'before/head'}, {'view': 'after/head'}],
                   'before_context': {'work_regions': []}}
        value = {'checks': {'layout:0': {'status': 'pass', 'reason': 'fixture', 'views': ['before/head', 'after/head']}},
                 'information_request': None, 'agent_assessment': {'conclusion': 'unknown', 'reason': 'not tested', 'preferred_regions': []}}
        schema = output_schema('reviewer', payload)
        validate_output(value, schema)
        for bad in ({**value, 'intent': 'irrelevant'}, {**value, 'checks': {}}, {**value, 'task_success': True}):
            with self.assertRaises(ContractError):
                validate_output(bad, schema)

    def test_static_template_cannot_require_actual_base_motion(self):
        request = SceneConstructionRequest('几何布局变化', SceneInput('single', scene_id='x', xml_path='x'), 'case1')
        value = {**self.value, 'semantic_checks': ['底盘移动后抓取成功率提高']}
        gateway = CaseGateway(lambda **kw: value, AgentBudget(1, time.monotonic()+10), format_retries=0)
        with self.assertRaises(AgentFormatError):
            normalize_case(request, gateway)

    def test_nonproof_disclaimer_is_not_actual_motion_requirement(self):
        request = SceneConstructionRequest('几何布局变化', SceneInput('single', scene_id='x', xml_path='x'), 'case1')
        value = {**self.value, 'semantic_checks': ['目标与障碍布局发生变化，不将布局外观作为机械臂可达、抓取成功的证明']}
        gateway = CaseGateway(lambda **kw: value, AgentBudget(1, time.monotonic()+10))
        self.assertEqual(normalize_case(request, gateway).semantic_checks, value['semantic_checks'])

    def test_information_requests_are_typed(self):
        self.assertEqual(InformationRequest.from_dict({'kind': 'aux_view', 'reason': 'need side'}).view_hint, 'auto')
        for value in ('need an image', {'kind': 'execute_code', 'reason': 'x'},
                      {'kind': 'expand_context', 'reason': 'x'}, {'kind': 'assets', 'reason': 'x'}):
            with self.assertRaises(ContractError):
                InformationRequest.from_dict(value)

    def test_boolean_conditions_require_value_in_wire_schema(self):
        schema = condition_schema()
        for predicate, args in [('supported', ['$target']), ('supported_by', ['$target', '$support']),
                                ('inside_region', ['$target', '$region'])]:
            good = {'predicate': predicate, 'args': args, 'value': True}
            validate_output(good, schema)
            for bad in ({'predicate': predicate, 'args': args}, {**good, 'value': None},
                        {**good, 'min': 0}, {**good, 'args': []}):
                with self.assertRaises(ContractError): validate_output(bad, schema)
        validate_output({'predicate': 'distance_xy', 'args': ['$a', '$b'], 'range': [1, 2]}, schema)
        validate_output({'predicate': 'direction_angle', 'args': [
            {'vector': [1, 0, 0], 'frame': 'world'}, '$target.x_axis'], 'max': 1}, schema)

    def test_wire_field_names_match_domain_contracts(self):
        self.assertEqual(set(output_schema('normalizer', self.payload)['properties']), {f.name for f in fields(MobileCaseTemplate)})
        payload = {'contexts': [], 'radius_budget_m': [2, 4], 'available_asset_categories': []}
        self.assertEqual(set(output_schema('strategist', payload)['properties']), {f.name for f in fields(ConstructionPlan)})

    def test_wire_operation_subject_requires_symbolic_role(self):
        schema = dsl_schema(['actual_node'])
        value = {'proposal_id': 'p', 'bindings': {'target': 'actual_node'},
                 'operations': [{'op': 'move', 'subject': '$target', 'search_space': {}}]}
        validate_output(value, schema)
        for subject in ('actual_node', '$', '$bad.role'):
            bad = copy.deepcopy(value); bad['operations'][0]['subject'] = subject
            with self.assertRaises(ContractError): validate_output(bad, schema)

    def test_provider_capability_fallback_charged_cached_and_model_override(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            original = {'providers': [{'name': 'first', 'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://one.example/v1', 'LLM_MODEL': 'gpt-5.6-sol'}]}
            path.write_text(json.dumps(original))
            backend = HTTPCaseBackend(path, model='glm-5.3')
            self.assertEqual(backend.public_provider()['model'], 'glm-5.3')
            requests = []
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(inner):
                    return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.value)}}],
                                       'usage': {'completion_tokens': 100}}).encode()
            def open_request(request, timeout):
                body = json.loads(request.data)
                requests.append(body)
                if body.get('response_format', {}).get('type') == 'json_schema':
                    raise urllib_error(request.full_url)
                return Response()
            def urllib_error(url):
                from urllib.error import HTTPError
                return HTTPError(url, 400, 'unsupported', {}, io.BytesIO(b'{"error":{"message":"response_format json_schema unsupported"}}'))
            gateway = CaseGateway(backend, AgentBudget(3, time.monotonic()+10))
            with patch('urllib.request.build_opener', return_value=Mock(open=open_request)):
                gateway.call('normalizer', 'JSON', self.payload, lambda value: value)
                gateway.call('normalizer', 'JSON', self.payload, lambda value: value)
            self.assertEqual(gateway.budget.calls, 3)
            self.assertEqual([r['response_format']['type'] for r in requests], ['json_schema', 'json_object', 'json_object'])
            self.assertTrue(all(r['model'] == 'glm-5.3' for r in requests))
            self.assertEqual(json.loads(path.read_text()), original)
            self.assertEqual(backend.last_metadata['finish_reason'], 'stop')

    def test_truncation_not_accepted_even_when_json_parses(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text(json.dumps({'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://one.example/v1', 'LLM_MODEL': 'm'}))
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(inner): return b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}'
            backend = HTTPCaseBackend(path)
            with patch('urllib.request.build_opener', return_value=Mock(open=Mock(return_value=Response()))):
                with self.assertRaises(AgentServiceError):
                    CaseGateway(backend, AgentBudget(1, time.monotonic()+10)).call('custom', 'JSON', {}, lambda value: value)

    def test_xera_only_text_transport_keeps_local_contract_and_images(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text(json.dumps({'provider': 'xera', 'providers': [
                {'name': 'dmx', 'enabled': False, 'LLM_API_KEY': 'unused', 'LLM_BASE_URL': 'https://disabled.example/v1', 'LLM_MODEL': 'm'},
                {'name': 'xera', 'enabled': True, 'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://xera.example/v1', 'LLM_MODEL': 'gpt-5.6-sol', 'structured_output': 'text'}]}))
            with self.assertRaises(ValueError):
                HTTPCaseBackend(path, provider='dmx')
            backend = HTTPCaseBackend(path, provider='auto')
            requests = []
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(inner): return json.dumps({'choices': [{'message': {'content': json.dumps(self.value)}}]}).encode()
            def open_request(request, timeout):
                requests.append(json.loads(request.data))
                self.assertIn('xera.example', request.full_url)
                return Response()
            image = Path(root)/'image.png'; image.write_bytes(b'fixture')
            gateway = CaseGateway(backend, AgentBudget(2, time.monotonic()+10))
            with patch('urllib.request.build_opener', return_value=Mock(open=open_request)):
                gateway.call('normalizer', 'JSON', self.payload, lambda value: value)
                gateway.call('normalizer', 'JSON', self.payload, lambda value: value,
                             images=[{'path': str(image), 'view': 'before/head'}])
            self.assertEqual(backend.public_provider()['name'], 'xera')
            self.assertFalse(backend.advance_provider())
            self.assertTrue(all('response_format' not in r for r in requests))
            self.assertIsInstance(requests[0]['messages'][1]['content'], str)
            self.assertIn('output_contract', json.loads(requests[0]['messages'][1]['content']))
            self.assertEqual(requests[1]['messages'][1]['content'][-1]['type'], 'image_url')

    def test_nonfinite_bad_response_can_be_repaired_and_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            seen = []
            def backend(**kwargs):
                seen.append(kwargs['payload'])
                return self.value if len(seen) > 1 else '{"not_finite": NaN}'
            gateway = CaseGateway(backend, AgentBudget(2, time.monotonic()+10), path=root)
            gateway.call('normalizer', 'JSON', self.payload, lambda value: value)
            self.assertIsInstance(seen[1]['previous_response'], str)
            self.assertEqual(json.loads((Path(root)/'0001-normalizer.json').read_text())['status'], 'parsed')

    def test_explicit_json_schema_transport_still_locally_validated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text(json.dumps({'provider': 'xera', 'providers': [
                {'name': 'xera', 'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://xera.example/v1',
                 'LLM_MODEL': 'gpt-5.6-sol', 'structured_output': 'json_schema', 'json_schema_strict': False}]}))
            backend = HTTPCaseBackend(path)
            requests = []
            values = [{**self.value, 'roles': 'invalid'}, self.value]
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(inner): return json.dumps({'choices': [{'message': {'content': json.dumps(values.pop(0))}}]}).encode()
            def open_request(request, timeout):
                requests.append(json.loads(request.data)); return Response()
            with patch('urllib.request.build_opener', return_value=Mock(open=open_request)):
                CaseGateway(backend, AgentBudget(2, time.monotonic()+10)).call('normalizer', 'JSON', self.payload, lambda value: value)
            self.assertEqual(len(requests), 2)
            for body in requests:
                self.assertEqual(body['response_format']['type'], 'json_schema')
                self.assertFalse(body['response_format']['json_schema']['strict'])
                self.assertEqual(body['response_format']['json_schema']['schema'], output_schema('normalizer', self.payload))
            self.assertFalse(backend.downgrade_output())

    def test_text_only_parameter_error_does_not_drop_images(self):
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text(json.dumps({'provider': 'xera', 'providers': [
                {'name': 'xera', 'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://xera.example/v1', 'LLM_MODEL': 'glm-5.3', 'structured_output': 'text'}]}))
            error_body = json.dumps({'error': {'param': 'messages[1].content[1].type',
                'message': "messages.1.type 参数非法，取值范围 ['text']"}}, ensure_ascii=False).encode()
            opener = Mock(open=Mock(side_effect=HTTPError('https://xera.example/v1', 400, 'invalid', {}, io.BytesIO(error_body))))
            gateway = CaseGateway(HTTPCaseBackend(path), AgentBudget(2, time.monotonic()+10), path=root)
            image = Path(root)/'image.png'; image.write_bytes(b'fixture')
            with patch('urllib.request.build_opener', return_value=opener):
                with self.assertRaises(AgentServiceError):
                    gateway.call('custom', 'JSON', {}, lambda value: value,
                                 images=[{'path': str(image), 'view': 'before/head'}])
            self.assertEqual(opener.open.call_count, 1)
            body = json.loads(opener.open.call_args.args[0].data)
            self.assertEqual(body['messages'][1]['content'][-1]['type'], 'image_url')
            record = json.loads((Path(root)/'0000-custom.json').read_text())
            self.assertEqual(record['service_error_parameter'], 'messages[1].content[1].type')
            self.assertIn('type_text_only', record['service_error_categories'])


if __name__ == '__main__':
    unittest.main()
