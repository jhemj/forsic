"""Synthetic settings and bounded Ren routing; no provider/model network calls."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from forsic_plugin import connections as c, independent_review as review
import test_connections as connection_fixtures
import test_independent_review as review_fixtures
import test_review_overlap as overlap_fixtures


class RenConnectionTests(unittest.TestCase):
    setUp = connection_fixtures.ConnectionTests.setUp

    def save(self, **extra):
        with patch.object(c, 'config', return_value=self.cfg), patch.object(c, 'key', return_value=''), \
                patch('hermes_cli.config.save_config') as config_write, \
                patch('hermes_cli.config.save_env_value') as secret_write, \
                patch('hermes_cli.config.remove_env_value') as secret_remove, \
                patch('hermes_cli.config.require_env_writable'), patch('hermes_cli.config.get_env_path'), \
                patch('socket.socket.connect', side_effect=AssertionError('offline test forbids network')):
            result = c.save({**self.body, **extra})
        return result, config_write, secret_write, secret_remove

    def test_defaults_omission_disable_and_reenable_preserve_independent_details(self):
        result, _, _, _ = self.save()
        self.assertEqual(result['ren_llm'], {'enabled': False, 'base_url': '', 'model': '', 'key_configured': False})
        main = deepcopy(self.cfg['model'])
        result, _, _, _ = self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test/v1/', ren_llm_model='reviewer')
        expected = result['ren_llm']
        self.assertEqual(self.save()[0]['ren_llm'], expected)
        result, _, _, _ = self.save(ren_llm_enabled=False)
        self.assertEqual(result['ren_llm'], {**expected, 'enabled': False})
        self.assertEqual(self.save(ren_llm_enabled=True)[0]['ren_llm'], expected)
        self.assertEqual(self.cfg['model'], main)
        self.assertNotIn('llm_pending', c.preferences(self.cfg))

    def test_invalid_input_never_writes_config_or_secrets(self):
        for extra in ({'ren_llm_enabled': True}, {'ren_llm_enabled': 'yes'},
                      {'ren_llm_base_url': 'https://secret@ren.test/v1'}, {'ren_llm_model': 'auto'},
                      {'ren_llm_api_key': 'secret\nvalue'},
                      {'ren_llm_base_url': 'https://ren.test', 'ren_llm_api_key': 'PRIVATE', 'clear_ren_llm_key': True}):
            with self.subTest(extra=extra), patch.object(c, 'config', return_value=self.cfg), \
                    patch('hermes_cli.config.save_config') as saved, \
                    patch('hermes_cli.config.save_env_value') as secret:
                with self.assertRaises(ValueError):
                    c.save({**self.body, **extra})
                saved.assert_not_called(); secret.assert_not_called()

    def test_endpoint_change_requires_key_choice_and_secrets_are_separate(self):
        result, _, saved, _ = self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test/v1',
                                       ren_llm_model='reviewer', ren_llm_api_key='PRIVATE_REN_KEY')
        slot = c.preferences(self.cfg)['ren_llm']['key_env']
        self.assertTrue(slot.startswith('HERMES_CUSTOM_FORSIC_REN_'))
        saved.assert_called_once_with(slot, 'PRIVATE_REN_KEY')
        self.assertNotIn('PRIVATE_REN_KEY', json.dumps(self.cfg) + json.dumps(result))
        with patch.object(c, 'config', return_value=self.cfg), patch('hermes_cli.config.save_config') as saved:
            with self.assertRaises(ValueError):
                c.save({**self.body, 'ren_llm_base_url': 'https://second.test/v1'})
            saved.assert_not_called()
        _, _, saved, _ = self.save(ren_llm_base_url='https://second.test/v1', ren_llm_api_key='NEW_REN_KEY')
        second_slot = c.preferences(self.cfg)['ren_llm']['key_env']
        self.assertNotEqual(slot, second_slot)
        saved.assert_called_once_with(second_slot, 'NEW_REN_KEY')
        result, _, _, removed = self.save(ren_llm_base_url='https://third.test/v1', clear_ren_llm_key=True)
        removed.assert_not_called()  # The previous runtime may still reference its slot.
        self.assertFalse(result['ren_llm']['key_configured'])
        self.assertNotIn('key_env', c.preferences(self.cfg)['ren_llm'])

    def test_disabled_main_route_and_anonymous_second_route_never_borrow_main_key(self):
        self.cfg['model']['api_key'] = 'PRIVATE_MAIN_KEY'
        with patch.object(c, 'key', side_effect=AssertionError('must not read main secrets')):
            main, credential = c.review_connection_snapshot(self.cfg)
            self.assertEqual(credential, 'PRIVATE_MAIN_KEY')
            self.assertEqual(main['base_url'], self.cfg['model']['base_url'])
            self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test', ren_llm_model='reviewer')
            route, credential = c.review_connection_snapshot(self.cfg)
            self.assertEqual(route['default'], 'reviewer')
            self.assertEqual(credential, 'no-key-required')

    def test_explicit_blank_is_keyless_and_omission_preserves_saved_ren_key(self):
        for blank in ('', ' \t\n '):
            with self.subTest(blank=blank):
                self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test/v1',
                          ren_llm_model='reviewer', ren_llm_api_key='SYNTHETIC_REN_SECRET')
                slot = c.preferences(self.cfg)['ren_llm']['key_env']
                self.save()
                self.assertEqual(c.preferences(self.cfg)['ren_llm']['key_env'], slot)
                result, _, _, removed = self.save(ren_llm_api_key=blank)
                removed.assert_not_called()  # Retain slots bound by existing review snapshots.
                self.assertFalse(result['ren_llm']['key_configured'])
                with patch.object(c, 'key', side_effect=AssertionError('keyless Ren cannot read another key')):
                    _, credential = c.review_connection_snapshot(self.cfg)
                self.assertEqual(credential, 'no-key-required')

    def test_main_review_uses_only_configured_inline_or_native_reference(self):
        for field in ('key_env', 'api_key_env'):
            with self.subTest(field=field), patch.object(c, 'key', return_value='SYNTHETIC_MAIN_SECRET') as read:
                self.cfg['model'][field] = 'SCOPED_MAIN_KEY'
                route, credential = c.review_connection_snapshot(self.cfg)
                self.assertEqual(credential, 'SYNTHETIC_MAIN_SECRET')
                self.assertEqual(route['base_url'], self.body['llm_base_url'])
                read.assert_called_once_with('SCOPED_MAIN_KEY')
                del self.cfg['model'][field]
        self.cfg['model']['key_env'] = 'MISSING_MAIN_KEY'
        with patch.object(c, 'key', return_value=''):
            with self.assertRaises(ValueError):
                c.review_connection_snapshot(self.cfg)
        self.cfg['model']['base_url'] = 'https://SYNTHETIC_PRIVATE@bad.example/v1'
        with patch.object(c, 'key', side_effect=AssertionError('invalid endpoint must fail before reading credential')):
            with self.assertRaises(ValueError):
                c.review_connection_snapshot(self.cfg)

    def test_configured_key_missing_in_profile_fails_closed(self):
        self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test', ren_llm_model='reviewer', ren_llm_api_key='PRIVATE')
        with patch.object(c, 'key', return_value='') as key:
            with self.assertRaises(ValueError):
                c.review_connection_snapshot(self.cfg)
        key.assert_called_once_with(c.preferences(self.cfg)['ren_llm']['key_env'])

    def test_public_key_presence_and_main_credential_reference_rejection(self):
        self.save(ren_llm_enabled=True, ren_llm_base_url='https://ren.test', ren_llm_model='reviewer', ren_llm_api_key='PRIVATE')
        with patch.object(c, 'config', return_value=self.cfg), patch.object(c, 'key', return_value='PRIVATE'):
            public = c.public()['ren_llm']
        self.assertEqual(set(public), {'enabled', 'base_url', 'model', 'key_configured'})
        self.assertTrue(public['key_configured'])
        self.assertNotIn('PRIVATE', json.dumps(public))
        c.preferences(self.cfg)['ren_llm']['key_env'] = 'OPENAI_API_KEY'
        with patch.object(c, 'key', side_effect=AssertionError('must not read main credential')):
            with self.assertRaises(ValueError):
                c.review_connection_snapshot(self.cfg)


class RenConnectionApiTests(unittest.TestCase):
    setUp = connection_fixtures.ConnectionTests.setUp

    def test_http_settings_roundtrip_omission_validation_and_secret_masking(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from forsic_plugin.dashboard import plugin_api
        app = FastAPI()
        app.include_router(plugin_api.router, prefix='/api/plugins/forsic')
        client = TestClient(app)
        url = '/api/plugins/forsic/connections'
        secrets = {}
        with patch.object(c, 'config', return_value=self.cfg), \
                patch.object(c, 'key', side_effect=lambda name='VT_APIKEY': secrets.get(name, '')), \
                patch('hermes_cli.config.save_config') as write, \
                patch('hermes_cli.config.save_env_value', side_effect=lambda name, value: secrets.update({name: value})), \
                patch('hermes_cli.config.remove_env_value', side_effect=lambda name: secrets.pop(name, None)), \
                patch('hermes_cli.config.require_env_writable'), patch('hermes_cli.config.get_env_path'), \
                patch('socket.socket.connect', side_effect=AssertionError('no live provider request')):
            initial = client.get(url)
            self.assertEqual(initial.status_code, 200, initial.text)
            self.assertFalse(initial.json()['ren_llm']['enabled'])
            payload = {**self.body, 'ren_llm_enabled': True, 'ren_llm_base_url': 'https://review.test/v1',
                       'ren_llm_model': 'separate-reviewer', 'ren_llm_api_key': 'SYNTHETIC_REN_SECRET'}
            saved = client.put(url, json=payload)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(saved.json()['ren_llm']['key_configured'])
            self.assertNotIn(payload['ren_llm_api_key'], saved.text)
            self.assertNotIn(payload['ren_llm_api_key'], repr(plugin_api.ConnectionInput(**payload)))
            expected = saved.json()['ren_llm']
            # Old clients do not erase a configured second connection.
            self.assertEqual(client.put(url, json=self.body).json()['ren_llm'], expected)
            self.assertEqual(client.get(url).json()['ren_llm'], expected)
            before = deepcopy(self.cfg)
            writes = write.call_count
            for extra, status in (({'ren_llm_enabled': 'true'}, 422),
                                  ({'ren_llm_enabled': None}, 400),
                                  ({'ren_llm_base_url': None}, 400),
                                  ({'ren_llm_model': ''}, 400),
                                  ({'ren_llm_base_url': 'https://other.test/v1'}, 400)):
                result = client.put(url, json={**self.body, **extra})
                self.assertEqual(result.status_code, status, result.text)
                self.assertEqual(self.cfg, before)
                self.assertEqual(write.call_count, writes)
            disabled = client.put(url, json={**self.body, 'ren_llm_enabled': False})
            self.assertEqual(disabled.status_code, 200)
            self.assertEqual(disabled.json()['ren_llm'], {**expected, 'enabled': False})
            for malformed in (None, ['SYNTHETIC_PRIVATE'], {'SYNTHETIC_PRIVATE': 'value'}):
                response = client.put(url, json={**self.body, 'ren_llm_api_key': malformed})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('SYNTHETIC_PRIVATE', response.text)
            cleared = client.put(url, json={**self.body, 'ren_llm_api_key': ' \t '})
            self.assertEqual(cleared.status_code, 200)
            self.assertFalse(cleared.json()['ren_llm']['key_configured'])
            self.assertIn('SYNTHETIC_REN_SECRET', secrets.values())
            self.assertEqual(self.cfg['model']['default'], 'qwen')


class RenAtomicPersistenceTests(unittest.TestCase):
    from test_setup import SetupFixture
    setUp = SetupFixture.setUp
    cfg = SetupFixture.cfg

    def test_same_endpoint_replacement_and_clear_keep_old_credential_if_config_save_fails(self):
        from hermes_cli.config import load_env
        body = {'llm_base_url': self.body['llm_base_url'], 'llm_model': 'old',
                'ren_llm_enabled': True, 'ren_llm_base_url': 'https://ren.test/v1', 'ren_llm_model': 'reviewer'}
        with patch('socket.socket.connect', side_effect=AssertionError('offline connection test')):
            c.save({**body, 'ren_llm_api_key': 'SYNTHETIC_ORIGINAL_REN'})
            slot = c.preferences(self.cfg())['ren_llm']['key_env']
            frozen_route, frozen_key = c.review_connection_snapshot(self.cfg())
            config_before = (self.home / 'config.yaml').read_bytes()
            for choice in ({'ren_llm_api_key': 'SYNTHETIC_NEW_REN'}, {'ren_llm_api_key': ''}):
                with self.subTest(choice=choice), patch('hermes_cli.config.save_config', side_effect=OSError('synthetic write failure')):
                    with self.assertRaises(OSError):
                        c.save({**body, **choice})
                self.assertEqual((self.home / 'config.yaml').read_bytes(), config_before)
                self.assertEqual(load_env()[slot], 'SYNTHETIC_ORIGINAL_REN')
                self.assertEqual(c.review_connection_snapshot(self.cfg())[1], 'SYNTHETIC_ORIGINAL_REN')
            c.save({**body, 'ren_llm_api_key': 'SYNTHETIC_NEW_REN'})
            new_slot = c.preferences(self.cfg())['ren_llm']['key_env']
            self.assertNotEqual(slot, new_slot)
            self.assertIn(slot, load_env())
            self.assertEqual(c.review_connection_snapshot(self.cfg())[1], 'SYNTHETIC_NEW_REN')
            self.assertEqual(frozen_key, 'SYNTHETIC_ORIGINAL_REN')
            self.assertEqual(frozen_route['base_url'], body['ren_llm_base_url'])
            c.save({**body, 'ren_llm_api_key': ' \n '})
            self.assertIn(new_slot, load_env())
            self.assertEqual(c.review_connection_snapshot(self.cfg())[1], 'no-key-required')


class RenRoutingTests(unittest.TestCase):
    setUp = review_fixtures.IndependentReviewTests.setUp
    bind = review_fixtures.IndependentReviewTests.bind
    session = review_fixtures.IndependentReviewTests.session
    save_note = review_fixtures.IndependentReviewTests.save_note
    add_sources = review_fixtures.IndependentReviewTests.add_sources
    setup_review = review_fixtures.IndependentReviewTests.setup_review
    response = review_fixtures.IndependentReviewTests.response
    kwargs = review_fixtures.IndependentReviewTests.kwargs
    request_paths = review_fixtures.IndependentReviewTests.request_paths

    def configure_ren(self):
        self.setup_review()
        self.ren = {'enabled': True, 'base_url': 'https://ren.test/v1', 'model': 'independent-ren', 'key_env': c._ren_key_env('https://ren.test/v1')}
        self.config['plugins'] = {'entries': {'forsic': {'settings': {'connections': {'ren_llm': self.ren}}}}}
        self.stack.enter_context(patch.object(c, 'key', return_value='PRIVATE_REN_KEY'))

    def test_second_route_has_blind_history_and_public_provenance_without_secret(self):
        self.configure_ren()
        result = review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'], 'received')
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual((call['model'], call['base_url'], call['api_key']),
                         ('independent-ren', 'https://ren.test/v1', 'PRIVATE_REN_KEY'))
        self.assertEqual([m['role'] for m in call['messages']], ['system', 'user'])
        self.assertNotIn('PRIVATE_MAIN_ANSWER', json.dumps(call['messages']))
        self.assertNotIn('PRIVATE_HISTORY', json.dumps(call['messages']))
        receipts = ''.join(path.read_text() for path in self.case.output.glob('independent-review-*.json'))
        self.assertNotIn('PRIVATE_REN_KEY', receipts + json.dumps(result))
        request = json.loads(self.request_paths()[0].read_text())
        self.assertEqual(request['model']['connection'], 'ren')
        self.assertEqual(request['model']['default'], 'independent-ren')

    def test_second_failure_and_missing_config_never_fall_back(self):
        self.configure_ren()
        self.fake.side_effect = RuntimeError('PRIVATE_REN_KEY provider failure')
        result = review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'], 'delivery_unconfirmed')
        self.assertEqual(self.fake.call_count, 1)
        self.assertEqual(self.fake.call_args.kwargs['model'], 'independent-ren')
        receipts = ''.join(path.read_text() for path in self.case.output.glob('independent-review-*.json'))
        self.assertNotIn('PRIVATE_REN_KEY', receipts + json.dumps(result))
        # A separate goal with invalid enabled route makes zero requests.
        self.ren['model'] = ''
        self.add_sources(1); self.save_note(answer='A changed interpretation')
        result = review.goal_advisory_context(**self.kwargs(stage='final', draft_response='Changed draft'))
        self.assertEqual(self.fake.call_count, 1)
        self.assertEqual(result['status'], 'unavailable')


class RenOverlapTests(unittest.TestCase):
    setUp = overlap_fixtures.ReviewOverlapTests.setUp
    bind = overlap_fixtures.ReviewOverlapTests.bind
    session = overlap_fixtures.ReviewOverlapTests.session
    save_note = overlap_fixtures.ReviewOverlapTests.save_note
    setup_review = overlap_fixtures.ReviewOverlapTests.setup_review
    add_sources = overlap_fixtures.ReviewOverlapTests.add_sources
    response = overlap_fixtures.ReviewOverlapTests.response
    kwargs = overlap_fixtures.ReviewOverlapTests.kwargs
    request_paths = overlap_fixtures.ReviewOverlapTests.request_paths
    blocked = overlap_fixtures.ReviewOverlapTests.blocked
    finish_workers = overlap_fixtures.ReviewOverlapTests.finish_workers
    resolved = overlap_fixtures.ReviewOverlapTests.resolved
    launch = overlap_fixtures.ReviewOverlapTests.launch
    settle = overlap_fixtures.ReviewOverlapTests.settle

    def test_route_and_key_frozen_before_worker_dispatch_then_next_review_uses_new_choice(self):
        from forsic_plugin import independent_review_overlap as overlap
        ren = {'enabled': True, 'base_url': 'https://ren.test/v1', 'model': 'first-ren', 'key_env': c._ren_key_env('https://ren.test/v1')}
        self.config['plugins'] = {'entries': {'forsic': {'settings': {'connections': {'ren_llm': ren}}}}}
        original = overlap.start
        # Change the live choice after receipt creation, before starting the worker.
        def changed_start(*args, **kwargs):
            ren.update(enabled=False, base_url='https://changed.test/v1', model='changed-ren')
            return original(*args, **kwargs)
        with patch.object(c, 'key', return_value='SNAPSHOT_REN_KEY'), patch.object(overlap, 'start', side_effect=changed_start):
            self.launch()
        self.assertEqual((self.calls[0]['model'], self.calls[0]['base_url'], self.calls[0]['api_key']),
                         ('first-ren', 'https://ren.test/v1', 'SNAPSHOT_REN_KEY'))
        self.settle()
        self.fake.side_effect = review_fixtures.IndependentReviewTests.empty_response.__get__(self)
        review.goal_advisory_context(**self.kwargs(stage='final', draft_response='Current synthetic draft'))
        self.assertEqual(self.calls[-1]['model'], self.config['model']['default'])
        self.assertEqual(self.calls[-1]['api_key'], 'no-key-required')


class RenTenantInheritanceTests(unittest.TestCase):
    def test_new_tenant_keeps_native_secret_reference_without_credentials_or_history(self):
        from forsic_plugin.tenants import TenantRegistry, bind_scope, _config
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            home = Path(folder) / 'home'; home.mkdir()
            stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(home)}))
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('offline test forbids network')))
            ren = {'enabled': True, 'base_url': 'https://ren.test/v1', 'model': 'reviewer',
                   'key_env': c._ren_key_env('https://ren.test/v1'), 'api_key': 'PRIVATE_INLINE'}
            (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'custom', 'default': 'main'},
                'plugins': {'entries': {'forsic': {'settings': {'connections': {'ren_llm': ren}}}}}}))
            (home / '.env').write_text(ren['key_env'] + '=PRIVATE_SOURCE_SECRET\n')
            tenant = TenantRegistry().create('Synthetic Ren Tenant', operation_id=str(uuid.uuid4()))
            config = _config(tenant['profile_home'])
            inherited = c.preferences(config)['ren_llm']
            self.assertEqual(inherited, {k: v for k, v in ren.items() if k != 'api_key'})
            self.assertNotIn('PRIVATE_', json.dumps(config))
            self.assertNotIn('PRIVATE_', (Path(tenant['profile_home']) / '.env').read_text())
            self.assertFalse(Path(tenant['native_db']).exists())
            with bind_scope(tenant), self.assertRaises(ValueError):
                c.review_connection_snapshot(config)
