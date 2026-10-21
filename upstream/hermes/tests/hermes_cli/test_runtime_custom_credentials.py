"""Explicit custom-model credentials must outrank ambient provider pools/keys."""
from contextlib import contextmanager

import pytest

from agent.secret_scope import (
    build_profile_secret_scope, reset_secret_scope, set_secret_scope,
)
from hermes_cli import auth, config, runtime_provider
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@contextmanager
def profile(home):
    home_token = set_hermes_home_override(home)
    secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    try:
        yield
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


def configure(home, url, choice, pool_kind='named'):
    home.mkdir(exist_ok=True)
    with profile(home):
        config.save_config({'model': {'provider': 'custom', 'base_url': url, 'default': 'synthetic', **choice},
                            'custom_providers': [{'name': 'ambient', 'base_url': url}]})
        config.save_env_value('OPENAI_API_KEY', 'SYNTHETIC_AMBIENT_OPENAI')
        config.save_env_value('OPENAI_BASE_URL', url)
        config.save_env_value('OPENROUTER_API_KEY', 'sk-or-SYNTHETIC_AMBIENT_OPENROUTER')
        config.save_env_value('CHOSEN_CUSTOM_KEY', 'SYNTHETIC_EXPLICIT_CUSTOM')
        auth.write_credential_pool('custom:ambient' if pool_kind == 'named' else 'custom', [{
            'id': 'ambient', 'label': 'synthetic', 'auth_type': 'api_key', 'priority': 0,
            'source': 'manual', 'access_token': 'SYNTHETIC_AMBIENT_POOL', 'base_url': url,
        }])


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:11434/v1', 'http://192.168.10.20:8080/v1',
    'https://synthetic.example/v1', 'https://openrouter.ai/api/v1', 'https://api.openai.com/v1',
])
@pytest.mark.parametrize('choice,expected', [
    ({'api_key': 'no-key-required'}, 'no-key-required'),
    ({'key_env': 'CHOSEN_CUSTOM_KEY'}, 'SYNTHETIC_EXPLICIT_CUSTOM'),
    ({'key_env': 'MISSING_CUSTOM_KEY'}, 'no-key-required'),
])
@pytest.mark.parametrize('pool_kind', ['named', 'generic'])
def test_custom_config_choice_survives_ambient_credentials_and_profile_switch(tmp_path, monkeypatch, url, choice, expected, pool_kind):
    monkeypatch.setattr('socket.socket.connect', lambda *args: pytest.fail('offline provider test'))
    a, b = tmp_path / 'a', tmp_path / 'b'
    configure(a, url, choice, pool_kind)
    configure(b, url, {'api_key': 'SYNTHETIC_OTHER_PROFILE'}, pool_kind)
    for home, credential in ((a, expected), (b, 'SYNTHETIC_OTHER_PROFILE'), (a, expected)):
        with profile(home):
            result = runtime_provider.resolve_runtime_provider(requested='custom')
        assert result['base_url'] == url
        assert result['api_key'] == credential
        assert not result.get('credential_pool')


def test_omitted_custom_credential_still_uses_native_endpoint_pool(tmp_path, monkeypatch):
    monkeypatch.setattr('socket.socket.connect', lambda *args: pytest.fail('offline provider test'))
    url = 'https://synthetic.example/v1'
    home = tmp_path / 'home'
    configure(home, url, {})
    with profile(home):
        result = runtime_provider.resolve_runtime_provider(requested='custom')
    assert result['base_url'] == url
    assert result['api_key'] == 'SYNTHETIC_AMBIENT_POOL'
    assert result['credential_pool']
