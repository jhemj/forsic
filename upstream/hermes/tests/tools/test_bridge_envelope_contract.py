"""Rejected bridge batches dispatch nothing; schemas preserve connector batching."""
import json

import jsonschema
import pytest

from tools import tool_search
from tools.registry import registry


@pytest.mark.parametrize('names', [
    ['fixture_bridge_a', 'fixture_bridge_b'],
    ['connectors__fixture__read', 'fixture_bridge_a'],
    ['fixture_bridge_a', 'connectors__fixture__read'],
])
def test_rejected_multi_local_and_mixed_batches_dispatch_no_entries(monkeypatch, names):
    import model_tools

    dispatched = []
    definitions = []
    for name in ('fixture_bridge_a', 'fixture_bridge_b'):
        schema = {'name': name, 'parameters': {'type': 'object', 'properties': {}}}
        registry.register(name, 'fixture', schema, lambda args, **kw: dispatched.append(args) or 'ok')
        definitions.append({'type': 'function', 'function': schema})
    monkeypatch.setattr(model_tools, 'get_tool_definitions', lambda **kw: definitions)
    try:
        result = model_tools.handle_function_call('tool_call', {
            'calls': [{'name': name, 'arguments': {}} for name in names]}, task_id='synthetic')
        assert 'exactly one entry' in json.loads(result)['error']
        assert dispatched == []
    finally:
        for name in ('fixture_bridge_a', 'fixture_bridge_b'):
            registry.deregister(name)


def test_schema_and_resolver_allow_single_local_and_multi_connector_envelopes():
    schema = next(item['function']['parameters'] for item in tool_search.bridge_tool_schemas(deferred_count=2)
                  if item['function']['name'] == 'tool_call')
    for calls in ([{'name': 'fixture_local', 'arguments': {}}], [
        {'name': 'connectors__fixture__read', 'arguments': {}},
        {'name': 'connectors__fixture__search', 'arguments': {'query': 'synthetic'}},
    ]):
        jsonschema.validate({'calls': calls}, schema)
    batch = {'calls': [
        {'name': 'connectors__fixture__read', 'arguments': {}},
        {'name': 'connectors__fixture__search', 'arguments': {}},
    ]}
    name, args, error = tool_search.resolve_underlying_call(batch)
    assert error is None and name == tool_search.CONNECTOR_BATCH_SENTINEL and args == batch
    for invalid in ({'calls': []}, {'calls': [{'name': 'fixture_local', 'arguments': {}, 'extra': True}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)
