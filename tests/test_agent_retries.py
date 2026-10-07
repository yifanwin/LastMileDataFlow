"""Five total attempts, shared protocol/network budget, no live API calls."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from lastmile_dataflow.agents.case_gateway import (
    AgentBudget, AgentFormatError, AgentServiceError, BudgetExhausted,
    CaseGateway, HTTPCaseBackend,
)


class Response:
    def __init__(self, content='{}'):
        self.content = content

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self):
        return json.dumps({'choices': [{'message': {'content': self.content}}]}).encode()


def http_error(status, body=b'{}'):
    return HTTPError('https://xera.example/v1/chat/completions', status, 'failure', {}, io.BytesIO(body))


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root/'api.json'
        config.write_text(json.dumps({'provider': 'xera', 'providers': [{
            'name': 'xera', 'LLM_API_KEY': 'private', 'LLM_BASE_URL': 'https://xera.example/v1',
            'LLM_MODEL': 'gpt-5.6-sol', 'structured_output': 'json_schema'}]}))
        self.backend = HTTPCaseBackend(config)

    def gateway(self, **kwargs):
        budget = kwargs.pop('budget', AgentBudget(20, time.monotonic()+100))
        return CaseGateway(self.backend, budget, path=self.root/'calls', **kwargs)

    def call(self, gateway, parser=lambda value: value, **kwargs):
        return gateway.call('custom', 'JSON', {'keep': 'same candidate'}, parser, **kwargs)

    def test_transient_http_timeout_connection_and_backoff_preserve_images(self):
        image = self.root/'view.png'; image.write_bytes(b'fixture')
        opener = Mock(open=Mock(side_effect=[http_error(429), TimeoutError(), URLError('offline'), Response()]))
        gateway = self.gateway()
        with patch('urllib.request.build_opener', return_value=opener), patch('lastmile_dataflow.agents.case_gateway.time.sleep') as sleep:
            self.assertEqual(self.call(gateway, images=[{'path': str(image), 'view': 'before/head'}]), {})
        self.assertEqual(gateway.budget.calls, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2., 4., 8.])
        requests = [json.loads(c.args[0].data) for c in opener.open.call_args_list]
        self.assertTrue(all(r == requests[0] for r in requests))
        records = [json.loads(p.read_text()) for p in sorted((self.root/'calls').glob('*.json'))]
        self.assertEqual([r['attempt'] for r in records], [1, 2, 3, 4])
        self.assertEqual([r['status'] for r in records], ['service_retry']*3+['parsed'])
        self.assertTrue(all(r['max_attempts'] == 5 for r in records))

    def test_persistent_503_stops_at_five_total_attempts(self):
        opener = Mock(open=Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(503))))
        gateway = self.gateway()
        with patch('urllib.request.build_opener', return_value=opener), patch('lastmile_dataflow.agents.case_gateway.time.sleep') as sleep:
            with self.assertRaises(AgentServiceError): self.call(gateway)
        self.assertEqual(opener.open.call_count, 5)
        self.assertEqual(gateway.budget.calls, 5)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2., 4., 8., 16.])

    def test_format_and_network_attempts_share_five_not_twenty_five(self):
        opener = Mock(open=Mock(side_effect=[Response('invalid'), http_error(502), Response('invalid'),
                                            http_error(503), Response('invalid')]))
        gateway = self.gateway(retry_delay_s=0)
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(AgentFormatError): self.call(gateway)
        self.assertEqual(opener.open.call_count, 5)
        self.assertEqual(gateway.budget.calls, 5)
        requests = [json.loads(c.args[0].data) for c in opener.open.call_args_list]
        payloads = [json.loads(r['messages'][1]['content']) for r in requests]
        self.assertEqual(payloads[1]['previous_response'], 'invalid')
        self.assertEqual(payloads[2], payloads[1])
        self.assertTrue(all(p['keep'] == 'same candidate' for p in payloads))

    def test_permanent_errors_and_insufficient_quota_are_not_retried(self):
        for status, body in [(400, b'{}'), (401, b'{}'), (403, b'{}'),
                             (400, b'{"error":{"message":"response_format json_schema unsupported"}}'),
                             (429, b'{"error":{"code":"insufficient_quota"}}')]:
            with self.subTest(status=status, body=body):
                opener = Mock(open=Mock(side_effect=http_error(status, body)))
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(AgentServiceError): self.call(self.gateway())
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(self.backend.output_mode(), 'json_schema')

    def test_shared_call_budget_overrides_five(self):
        opener = Mock(open=Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(503))))
        gateway = self.gateway(budget=AgentBudget(2, time.monotonic()+100), retry_delay_s=0)
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(BudgetExhausted): self.call(gateway)
        self.assertEqual(opener.open.call_count, 2)

    def test_backoff_does_not_exceed_global_deadline(self):
        opener = Mock(open=Mock(side_effect=http_error(503)))
        gateway = self.gateway(budget=AgentBudget(20, time.monotonic()+1))
        with patch('urllib.request.build_opener', return_value=opener), patch('lastmile_dataflow.agents.case_gateway.time.sleep') as sleep:
            with self.assertRaisesRegex(BudgetExhausted, 'retry_backoff'): self.call(gateway)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_max_attempt_bounds_and_explicit_smaller_limit(self):
        for value in (0, 6, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError): self.gateway(max_attempts=value)
        gateway = self.gateway(max_attempts=1)
        opener = Mock(open=Mock(side_effect=http_error(503)))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(AgentServiceError): self.call(gateway)
        self.assertEqual(opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
