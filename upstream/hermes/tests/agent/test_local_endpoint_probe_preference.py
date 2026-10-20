"""Server discovery revalidates a known endpoint before probing other dialects."""
import json
import time

import httpx
import pytest

from agent import model_metadata as metadata


@pytest.fixture
def probe(monkeypatch, tmp_path):
    cache = tmp_path / 'local_endpoint_probes.json'
    monkeypatch.setattr(metadata, '_local_probe_disk_cache_path', lambda: cache)
    monkeypatch.setattr(metadata, '_endpoint_probe_path_cache', {})
    monkeypatch.setattr(metadata, '_endpoint_blackhole_cache', {})
    requests = []
    responses = {}
    real_client = httpx.Client

    def handle(request):
        requests.append(request.url.path)
        status, body = responses.get(request.url.path, (404, {'error': 'unknown path'}))
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(
        transport=httpx.MockTransport(handle), **kw))

    def seed(value, age=3600):
        cache.write_text(json.dumps({'server_type:http://127.0.0.1:12345': {
            'value': value, 'ts': time.time() - age}}))

    return seed, requests, responses


def test_expired_ollama_hint_is_revalidated_without_lm_studio_request(probe):
    seed, requests, responses = probe
    seed('ollama')
    responses['/api/tags'] = (200, {'models': []})
    assert metadata.detect_local_server_type('http://127.0.0.1:12345/v1') == 'ollama'
    assert requests == ['/api/tags']


def test_expired_hint_does_not_pin_a_server_that_changed(probe):
    seed, requests, responses = probe
    seed('ollama')
    responses['/api/v1/models'] = (200, {'models': []})
    assert metadata.detect_local_server_type('http://127.0.0.1:12345/v1') == 'lm-studio'
    assert requests == ['/api/tags', '/api/v1/models']


def test_hint_still_requires_expected_response_body(probe):
    seed, requests, responses = probe
    seed('ollama')
    responses['/api/tags'] = (200, {'error': 'not implemented'})
    assert metadata.detect_local_server_type('http://127.0.0.1:12345/v1') is None
    assert requests == ['/api/tags', '/api/v1/models', '/v1/props', '/props', '/version']


@pytest.mark.parametrize('hint', ['unknown-kind', {'invalid': 'ollama'}])
def test_unrecognized_cache_value_keeps_original_discovery(probe, hint):
    seed, requests, responses = probe
    seed(hint, age=0)
    responses['/api/tags'] = (200, {'models': []})
    assert metadata.detect_local_server_type('http://127.0.0.1:12345/v1') == 'ollama'
    assert requests == ['/api/v1/models', '/api/tags']


def test_fresh_known_verdict_still_avoids_all_requests(probe):
    seed, requests, _ = probe
    seed('ollama', age=0)
    assert metadata.detect_local_server_type('http://127.0.0.1:12345/v1') == 'ollama'
    assert requests == []
