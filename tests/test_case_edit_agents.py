import tempfile
import time
import json
import io
import urllib.error
from unittest.mock import patch, Mock
import unittest
from pathlib import Path
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.agents.case_gateway import (AgentBudget, CaseGateway, AgentFormatError,
    AgentServiceError, BudgetExhausted, HTTPCaseBackend)
from lastmile_dataflow.agents.case_normalizer import normalize_case
from lastmile_dataflow.agents.edit_proposer import propose_edits
from lastmile_dataflow.construction.case_schema import CaseEditRequest
from lastmile_dataflow.runtime.preparation import prepare_scene


class AgentTests(unittest.TestCase):
    def gateway(self, backend, calls=10, retries=1):
        return CaseGateway(backend, AgentBudget(calls, time.monotonic()+10), format_retries=retries)

    def test_each_request_normalized_no_cache(self):
        seen = []
        def backend(**kwargs):
            seen.append(kwargs)
            return template().to_dict()
        gateway = self.gateway(backend)
        request = CaseEditRequest('Visible supported layout change', {'scene_id': 'a', 'xml_path': 'a.xml'})
        a, b = normalize_case(request, gateway), normalize_case(request, gateway)
        self.assertEqual(len(seen), 2)
        self.assertEqual(a.source_description, request.case_description)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertNotIn('graph', seen[0]['payload'])

    def test_format_retry_shared_budget_service_errors(self):
        responses = iter(['not json', template().to_dict()])
        gateway = self.gateway(lambda **k: next(responses), calls=2)
        request = CaseEditRequest('Change layout', {'scene_id': 'a', 'xml_path': 'a.xml'})
        normalize_case(request, gateway)
        self.assertEqual(gateway.budget.calls, 2)
        with self.assertRaises(BudgetExhausted):
            normalize_case(request, gateway)
        with self.assertRaises(AgentFormatError):
            normalize_case(request, self.gateway(lambda **k: {'protected': []}, retries=0))
        def unavailable(**k):
            raise OSError('service offline')
        with self.assertRaises(AgentServiceError):
            normalize_case(request, self.gateway(unavailable))

    def test_scene_bound_proposals_and_unknown_operation(self):
        with tempfile.TemporaryDirectory() as root:
            source, robot = table_scene(root)
            with prepare_scene(source, robot, base=[0, 0, 0]) as prepared:
                parsed = propose_edits(template(), prepared.graph,
                    self.gateway(lambda **k: {'proposals': [proposal().to_dict()]}))
                self.assertEqual(parsed[0].bindings['support'], 'table')
                bad = proposal().to_dict()
                bad['bindings']['target'] = 'imaginary'
                with self.assertRaises(AgentFormatError):
                    propose_edits(template(), prepared.graph, self.gateway(lambda **k: {'proposals': [bad]}, retries=0))
                bad['operations'][0]['op'] = 'teleport_robot'
                with self.assertRaises(AgentFormatError):
                    propose_edits(template(), prepared.graph, self.gateway(lambda **k: {'proposals': [bad]}, retries=0))

    def test_two_channels_failover_charged_and_credentials_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text(json.dumps({'LLM_API_KEY':'primary-key', 'LLM_BASE_URL':'https://one.example/v1',
                'LLM_MODEL':'model', 'channels':[{'name':'channel_2', 'LLM_API_KEY':'secondary-key',
                'LLM_BASE_URL':'https://two.example/v1', 'LLM_MODEL':'model'}]}))
            backend = HTTPCaseBackend(path)
            requests = []
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self): return json.dumps({'choices':[{'message':{'content':json.dumps(template().to_dict())}}]}).encode()
            def open_request(request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    raise urllib.error.HTTPError(request.full_url, 403, 'denied', {}, io.BytesIO(b'{"error":{"code":"forbidden"}}'))
                return Response()
            with patch('urllib.request.build_opener', return_value=Mock(open=open_request)):
                gateway = self.gateway(backend, calls=2)
                request = CaseEditRequest('Visible layout change', {'scene_id':'a','xml_path':'a.xml'})
                normalize_case(request, gateway)
            self.assertEqual(gateway.budget.calls, 2)
            self.assertEqual([r.get_header('Authorization') for r in requests], ['Bearer primary-key','Bearer secondary-key'])
            self.assertEqual(backend.public_channel()['name'], 'channel_2')
            self.assertFalse(backend.advance_channel())
            requests.clear()
            with patch('urllib.request.build_opener', return_value=Mock(open=open_request)):
                with self.assertRaises(BudgetExhausted):
                    normalize_case(request, self.gateway(backend, calls=1))
            self.assertEqual(len(requests), 1)  # Cannot hide a fallback request outside budget.

    def test_provider_selection_explicit_and_auto_resets_priority(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            value = {'provider':'auto','providers':[
                {'name':'first','LLM_API_KEY':'one','LLM_BASE_URL':'https://one.example/v1','LLM_MODEL':'model'},
                {'name':'second','LLM_API_KEY':'two','LLM_BASE_URL':'https://two.example/v1','LLM_MODEL':'model'}]}
            path.write_text(json.dumps(value))
            auto = HTTPCaseBackend(path)
            self.assertEqual(auto.public_provider()['name'], 'first')
            self.assertTrue(auto.advance_provider())
            self.assertEqual(auto.public_provider()['name'], 'second')
            auto.begin_call()
            self.assertEqual(auto.public_provider()['name'], 'first')
            explicit = HTTPCaseBackend(path, provider='second')
            self.assertEqual(explicit.public_provider()['name'], 'second')
            self.assertFalse(explicit.advance_provider())
            explicit.begin_call()
            self.assertEqual(explicit.public_provider()['name'], 'second')
            with self.assertRaises(ValueError): HTTPCaseBackend(path, provider='missing')
            value['provider']='second'
            path.write_text(json.dumps(value))
            self.assertEqual(HTTPCaseBackend(path).public_provider()['name'], 'second')
            self.assertEqual(HTTPCaseBackend(path, provider='auto').public_provider()['name'], 'first')

    def test_blank_secondary_disabled_and_explicit_missing_key_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            value = {'LLM_API_KEY':'primary', 'LLM_BASE_URL':'https://one.example/v1', 'LLM_MODEL':'model',
                'channels':[{'name':'channel_2','LLM_API_KEY':'','LLM_BASE_URL':'https://two.example/v1','LLM_MODEL':'model'}]}
            path.write_text(json.dumps(value))
            backend = HTTPCaseBackend(path)
            self.assertFalse(backend.advance_channel())
            value['channels'][0]['enabled'] = True
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): HTTPCaseBackend(path)

    def test_credentials_not_in_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'api.json'
            path.write_text('{"LLM_API_KEY":"secret", "LLM_BASE_URL":"http://elsewhere.example/v1", "LLM_MODEL":"model"}')
            with self.assertRaises(ValueError):
                HTTPCaseBackend(path)
