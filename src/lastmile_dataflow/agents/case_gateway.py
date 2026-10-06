"""Three JSON suggestion protocols over a shared, bounded HTTP/model gateway."""
import base64
from dataclasses import dataclass
import json
import re
from pathlib import Path
import time
import urllib.parse
import urllib.error
import urllib.request

from .http_vision import RejectRedirects
from ..construction.case_schema import ContractError
from ..io import read_json, write_json


class AgentServiceError(RuntimeError):
    pass


class AgentFormatError(ValueError):
    pass


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class AgentBudget:
    max_calls: int
    deadline: float
    calls: int = 0

    def remaining_s(self):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise BudgetExhausted('wall_clock_budget_exhausted')
        return remaining

    def consume(self):
        self.remaining_s()
        if self.calls >= self.max_calls:
            raise BudgetExhausted('agent_call_budget_exhausted')
        index = self.calls
        self.calls += 1
        return index


class HTTPCaseBackend:
    def __init__(self, settings_path, *, max_tokens=6000, provider=None):
        path = Path(settings_path)
        if path.suffix == '.json':
            self.settings = read_json(path)
        else:
            from .http_vision import read_settings
            self.settings = read_settings(path)
        required = {'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'}
        value = self.settings
        if not isinstance(value, dict):
            raise ValueError('invalid model service settings')
        if 'providers' in value:
            if not {'providers'} <= set(value) <= {'providers', 'provider'} or not isinstance(value['providers'], list):
                raise ValueError('invalid providers configuration')
            records = value['providers']
            selection = provider if provider is not None else value.get('provider', 'auto')
        else:
            # Compatibility for old three-key JSON/.env and the earlier channels form.
            if not required <= set(value) <= required | {'channels', 'preferred_channel'}:
                raise ValueError('invalid model service settings')
            extras = value.get('channels', [])
            if not isinstance(extras, list):
                raise ValueError('channels must be an array')
            records = [{'name': 'channel_1', 'enabled': True, **{k: value[k] for k in required}}, *extras]
            selection = provider if provider is not None else value.get('preferred_channel', 'auto')
        for record in records:
            if not isinstance(record, dict) or not required | {'name'} <= set(record) <= required | {'name', 'enabled'}:
                raise ValueError('invalid provider settings')
        self.channels = []
        names = set()
        for record in records:
            name = record['name']
            enabled = record.get('enabled', bool(record.get('LLM_API_KEY')))
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', name) or name == 'auto' or name in names or type(enabled) is not bool:
                raise ValueError('invalid or duplicate channel name')
            names.add(name)
            settings = {k: record[k] for k in required}
            if not all(isinstance(v, str) for v in settings.values()) or not settings['LLM_BASE_URL'] or not settings['LLM_MODEL'] or (enabled and not settings['LLM_API_KEY']):
                raise ValueError('enabled channel requires its own key, URL and model')
            url = urllib.parse.urlsplit(settings['LLM_BASE_URL'])
            if url.scheme not in ('https', 'http') or (url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1')) or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('use HTTPS or localhost; no embedded credentials')
            if enabled:
                self.channels.append({'name': name, 'settings': settings})
        if not self.channels or not isinstance(selection, str) or (selection != 'auto' and selection not in {c['name'] for c in self.channels}):
            raise ValueError('selected provider is absent or disabled')
        self.provider_selection = selection
        self.channel_index = 0
        self.begin_call()
        self.max_tokens = max_tokens

    def begin_call(self):
        """Auto starts at the first provider for EACH logical Agent call."""
        self.channel_index = 0 if self.provider_selection == 'auto' else next(
            i for i, c in enumerate(self.channels) if c['name'] == self.provider_selection)
        self.settings = self.channels[self.channel_index]['settings']

    def public_provider(self):
        return {'name': self.channels[self.channel_index]['name'], 'selection': self.provider_selection,
                'endpoint_host': urllib.parse.urlsplit(self.settings['LLM_BASE_URL']).hostname,
                'model': self.settings['LLM_MODEL']}

    def advance_provider(self):
        """Bounded auto failover; explicit provider selection never falls back."""
        if self.provider_selection != 'auto' or self.channel_index + 1 >= len(self.channels):
            return False
        self.channel_index += 1
        self.settings = self.channels[self.channel_index]['settings']
        return True

    public_channel = public_provider
    advance_channel = advance_provider

    def __call__(self, *, role, system, payload, images, timeout_s):
        content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, allow_nan=False)}]
        for entry in images:
            path = Path(entry['path'])
            content += [{'type': 'text', 'text': 'view_id=' + entry['view']},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()}}]
        base = self.settings['LLM_BASE_URL'].rstrip('/')
        endpoint = base if base.endswith('/chat/completions') else base + '/chat/completions'
        body = {'model': self.settings['LLM_MODEL'], 'messages': [
            {'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
            'max_tokens': self.max_tokens}
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.settings['LLM_API_KEY']})
        try:
            with urllib.request.build_opener(RejectRedirects()).open(request, timeout=timeout_s) as response:
                output = json.load(response)
            return output['choices'][0]['message']['content']
        except Exception as exc:
            # Never emit headers/body/URL/key in artifacts or errors.
            error = AgentServiceError('model_service_failure:' + type(exc).__name__)
            error.service_error_type = type(exc).__name__
            error.http_status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            error.service_error_code = None
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    raw_error = exc.read()
                    value = json.loads(raw_error)
                    detail = value.get('error', {})
                    code = detail.get('code') or detail.get('type') if isinstance(detail, dict) else None
                    if isinstance(code, str) and self.settings['LLM_API_KEY'] not in code and re.fullmatch(r'[A-Za-z0-9_:-]{1,80}', code):
                        error.service_error_code = code
                except (ValueError, OSError, AttributeError):
                    pass  # Never log the raw body, headers or echoed request.
                raw_text = locals().get('raw_error', b'').decode('utf-8', errors='replace').lower()
                error.service_error_categories = [tag for tag in ('quota', 'balance', 'billing', 'rate',
                    'permission', 'forbidden', 'blocked', 'waf', 'image', 'moderation', 'content_policy') if tag in raw_text]
            raise error from None


class CaseGateway:
    def __init__(self, backend, budget, *, timeout_s=120., format_retries=1, path=None):
        if timeout_s <= 0 or type(format_retries) is not int or not 0 <= format_retries <= 3:
            raise ValueError('invalid agent timeout/retry configuration')
        self.backend, self.budget = backend, budget
        self.timeout_s, self.format_retries = timeout_s, format_retries
        self.path = Path(path) if path else None

    def call(self, role, system, payload, parser, *, images=None):
        last = None
        if hasattr(self.backend, 'begin_call'):
            self.backend.begin_call()
        format_attempts = 0
        while True:
            index = self.budget.consume()
            request = dict(payload)
            if last:
                request['format_feedback'] = last
            record = {'call': index, 'role': role, 'request': request,
                      'images': images or [], 'status': 'calling'}
            if hasattr(self.backend, 'public_provider'):
                record['provider'] = self.backend.public_provider()
            start = time.monotonic()
            if self.path:
                write_json(self.path / f'{index:04d}-{role}.json', record)
            try:
                raw = self.backend(role=role, system=system, payload=request, images=images or [],
                                   timeout_s=min(self.timeout_s, self.budget.remaining_s()))
                self.budget.remaining_s()
                value = None
                try:
                    value = json.loads(raw) if isinstance(raw, str) else raw
                    parsed = parser(value)
                except (ValueError, TypeError, KeyError) as exc:
                    last = 'invalid_structured_response:' + str(exc)[:500]
                    record.update(status='format_error', reason=last)
                    # Bad finite JSON can be retained without logging nonfinite values.
                    try:
                        json.dumps(value, allow_nan=False)
                        record['response'] = value
                    except (ValueError, TypeError, UnboundLocalError):
                        pass
                    if format_attempts == self.format_retries:
                        raise AgentFormatError(last) from None
                    format_attempts += 1
                    continue
                record.update(status='parsed', response=value)
                return parsed
            except (AgentFormatError, BudgetExhausted):
                raise
            except Exception as exc:
                record.update(status='service_error', error_type=type(exc).__name__,
                              service_error_type=getattr(exc, 'service_error_type', type(exc).__name__),
                              http_status=getattr(exc, 'http_status', None),
                              service_error_code=getattr(exc, 'service_error_code', None),
                              service_error_categories=getattr(exc, 'service_error_categories', []))
                if isinstance(exc, AgentServiceError) and hasattr(self.backend, 'advance_provider') and self.backend.advance_provider():
                    last = 'previous_channel_service_failed; use the same protocol on this configured channel'
                    continue  # A fresh consume() charges the alternate-channel call.
                raise AgentServiceError('agent_service_error:' + getattr(exc, 'service_error_type', type(exc).__name__)) from None
            finally:
                record['wall_time_s'] = time.monotonic()-start
                if self.path:
                    write_json(self.path / f'{index:04d}-{role}.json', record)


COMMON = '''Return exactly one JSON object, no markdown or code. You return suggestions;
the rule program executes them. Length m, time s, angle rad, world z up, right-handed,
quaternion [w,x,y,z]. Never claim robot reachability, task success, or physical proof
from abstract descriptions or images. Unknown evidence stays unknown. No permissions,
protected objects, approval labels or minimum-edit/nearest/smallest-change objectives.
Do not mistake pipeline process constraints for user-requested scene phenomena.'''

CONDITIONS = '''Conditions are objects: {"predicate":name,"args":[references],
"value":true/false} for supported(1), supported_by(2), inside_region(2), or
{"predicate":name,"args":[references],"range":[lo,hi]} / min / max for
distance_xy(2), distance_3d(2), direction_angle(2). Optional id, required=true,
tolerance=1e-6. References $role or $role.x_axis/y_axis/z_axis. Direction arguments
may be {"vector":[x,y,z],"frame":"world"} or {"node":"$role","axis_local":[x,y,z]}.
Do not invent other predicates. Human semantic directions without annotations are unknown.'''

DSL_HELP = '''A proposal has proposal_id, bindings {role:actual_node_id}, operations,
goals=[], invariants=[], sampling={"selection":"coverage","max_samples":16}.
move: {"op":"move","subject":"$role","search_space":{"support":"$support",
"region":"actual_region_id"(optional),"xy":{"mode":"uniform/grid/candidate",
"margin":0.01,"x":[lo,hi](optional),"y":[lo,hi](optional)},
"rotation":{"axis":[0,0,1],"angle":{"range":[lo,hi]},"frame":"world"}(optional)},
"carry_supported":false(optional)}. xy uses region-local coordinates, not world.
Alternatively move furniture with search_space frame="world", xy finite x/y ranges;
height stays at current value; offset {x,y,z} optional. A world:floor infinite plane
support needs explicit world x/y ranges (omit search_space.frame with support).
Explicit search_space.frame=world is ONLY for moves without support/concrete region. rotate: {"op":"rotate","subject":"$role",
"axis":[x,y,z],"angle":{"value":rad} or {"range":[lo,hi]},"frame":"world/object"};
angles are RELATIVE, not absolute yaw. remove: {"op":"remove","subject":"$role"}.
add: {"op":"add","bind_as":"$new","asset_selector":{"asset_id":"id"} or
{"category":["category"]},"search_space":same_as_move}. Newly added roles may be
used only after their add. Their support checks are after goals, NOT before invariants.
Conditions are additive to template, never replacements. Required roles cannot be
removed while retaining their after checks. Use only actually available operations/assets.'''
