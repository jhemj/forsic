"""Offline wizard and picker contracts; temporary synthetic files only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.intake import Intake
from forsic_plugin.investigations import Investigations
from forsic_plugin.onboarding import EvidenceBrowser, choices


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home, self.intake = self.root / 'runtime', self.root / 'intake'
        self.home.mkdir()
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.file = self.evidence / 'synthetic.log'
        self.file.write_text('synthetic evidence only\n')
        synthetic_home = patch('forsic_plugin.onboarding.Path.home', return_value=self.root)
        synthetic_home.start()
        self.addCleanup(synthetic_home.stop)
        self.catalog = Investigations(self.home, self.intake)
        self.browser = EvidenceBrowser(self.home, self.intake)

    def tearDown(self):
        self.tmp.cleanup()

    def test_choices_validation_and_native_prompt(self):
        for purpose in ('comprehensive', 'focused', 'report', 'timeline', 'custom'):
            selected = choices({'purpose': purpose, 'environment': 'unknown', 'question': '  확인 질문  '})
            self.assertEqual(selected['question'], '확인 질문')
            if purpose == 'comprehensive':
                self.assertEqual(selected['prompt'], '종합 침해 분석')
            else:
                self.assertIn('확인 질문', selected['prompt'])
                self.assertIn('증거에서 확인한 사실이 아닙니다', selected['prompt'])
        for value in (None, {}, {'purpose': 'bad', 'environment': 'linux'},
                      {'purpose': 'custom', 'environment': 'linux', 'question': '  '},
                      {'purpose': 'custom', 'environment': 'linux', 'question': 'x' * 4001}):
            with self.assertRaises(ValueError):
                choices(value)

    def test_report_choice_has_distinct_native_request_and_retry_identity(self):
        selected = {'purpose': 'report', 'environment': 'unknown', 'question': ''}
        normalized = choices(selected)
        self.assertIn('forsic-report-driven', normalized['prompt'])
        self.assertIn('references/audiences.md', normalized['prompt'])
        self.assertIn('기존 템플릿', normalized['prompt'])
        self.assertIn('forsic_reporting', normalized['prompt'])
        self.assertIn('이전 분석자의 결론·한계', normalized['prompt'])
        request = uuid.uuid4()
        created = self.catalog.create_conversation(request, path=str(self.file), onboarding=selected)
        self.assertEqual(created['onboarding'], normalized)
        self.assertEqual(self.catalog.create_conversation(request, path=str(self.file), onboarding=selected), created)
        self.assertEqual(Intake(self.intake).state(created['session_id'])['stage'], 'awaiting_question')
        from forsic_plugin.investigations import RequestConflict
        with self.assertRaises(RequestConflict):
            self.catalog.create_conversation(request, path=str(self.file),
                                             onboarding={**selected, 'purpose': 'timeline'})
        self.assertEqual(choices({**selected, 'purpose': 'timeline'})['prompt'],
                         '자료의 주요 사건과 시간 흐름을 근거에 따라 정리해줘.\n'
                         '사용자 지정 환경: 아직 모름 (증거에서 확인한 사실이 아닙니다).')

    def test_choices_persist_without_start_and_key_conflicts(self):
        import sqlite3
        request = uuid.uuid4()
        selected = {'purpose': 'comprehensive', 'environment': 'linux', 'question': '추가 범위'}
        created = self.catalog.create_conversation(request, path=str(self.file), onboarding=selected)
        self.assertEqual(self.catalog.create_conversation(request, path=str(self.file), onboarding=selected), created)
        state = Intake(self.intake).state(created['session_id'])
        manifest = json.loads(Path(state['manifest']).read_text())
        self.assertEqual(state['stage'], 'awaiting_question')
        self.assertEqual(state['onboarding'], created['onboarding'])
        self.assertEqual(manifest['onboarding'], created['onboarding'])
        self.assertNotIn('investigation_goal', manifest)
        with sqlite3.connect(self.home / 'state.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM state_meta WHERE key LIKE 'goal:%'").fetchone()[0], 0)
        from forsic_plugin.investigations import RequestConflict
        for changed in ({**selected, 'purpose': 'focused'}, {**selected, 'environment': 'windows'},
                        {**selected, 'question': '다른 질문'}, None):
            with self.assertRaises(RequestConflict):
                self.catalog.create_conversation(request, path=str(self.file), onboarding=changed)

    def test_choice_write_failure_recovers_same_case_and_session(self):
        from forsic_plugin.intake import write_json
        selected = {'purpose': 'focused', 'environment': 'mixed', 'question': '질문'}
        request = uuid.uuid4()
        def fail_binding_once(path, body):
            if body.get('onboarding') and body.get('stage'):
                raise OSError('synthetic interrupted binding write')
            return write_json(path, body)
        with patch('forsic_plugin.intake.write_json', side_effect=fail_binding_once):
            with self.assertRaises(OSError):
                self.catalog.create_conversation(request, path=str(self.file), onboarding=selected)
        receipt = json.loads((self.intake / 'ui-requests' / (str(request) + '.json')).read_text())
        state = Intake(self.intake).state(receipt['session_id'])
        self.assertNotIn('onboarding', state)
        self.assertEqual(json.loads(Path(state['manifest']).read_text())['onboarding']['question'], '질문')
        result = self.catalog.create_conversation(request, path=str(self.file), onboarding=selected)
        self.assertEqual(result['session_id'], receipt['session_id'])
        self.assertEqual(Intake(self.intake).state(result['session_id'])['onboarding'], result['onboarding'])
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)

    def test_api_onboarding_recovers_native_write_failure_and_normalizes_retry(self):
        from hermes_state import SessionDB
        selected = {'purpose': 'timeline', 'environment': 'macos', 'question': '시간대 확인'}
        body = api.InvestigationInput(request_id=uuid.uuid4(), path=str(self.file), onboarding=selected)
        with patch.object(api, 'directory', return_value=self.catalog):
            with patch.object(SessionDB, 'create_session', side_effect=RuntimeError('synthetic DB failure')):
                with self.assertRaises(RuntimeError):
                    api.investigation_create(body)
            result = api.investigation_create(body)
            self.assertEqual(result['onboarding'], choices(selected))
            self.assertEqual(api.investigation_create(body), result)
        state = Intake(self.intake).state(result['session_id'])
        self.assertEqual(state['stage'], 'awaiting_question')
        self.assertEqual(state['onboarding'], result['onboarding'])
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)

    def test_picker_defaults_to_synthetic_home_without_selecting_or_writing(self):
        expected = [{'name': self.evidence.name, 'path': str(self.evidence), 'kind': 'directory'}]
        for path in ('', '  '):
            result = self.browser.browse(path)
            self.assertEqual(result['path'], str(self.root))
            self.assertEqual(result['entries'], expected)
            self.assertFalse(result['has_more'])
            self.assertIsNone(result['next_offset'])
        for path in ('relative', '~/evidence', str(self.evidence / '..'), 'bad\x00path'):
            with self.assertRaises(ValueError):
                self.browser.browse(path)
        for path in (self.home, self.intake, Path('/etc'), Path(__file__).parent):
            with self.assertRaises(PermissionError):
                self.browser.browse(str(path))
        result = self.browser.browse(str(self.evidence))
        self.assertEqual(result['entries'], [{'name': self.file.name, 'path': str(self.file), 'kind': 'file'}])
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())

    def test_picker_sorted_pages_exclude_links_private_and_special_files(self):
        for n in range(105):
            (self.evidence / f'{n:03d}.txt').write_text('synthetic')
        (self.evidence / 'Folder').mkdir()
        (self.evidence / '.secret').write_text('synthetic hidden')
        (self.evidence / 'link').symlink_to(self.home, target_is_directory=True)
        os.mkfifo(self.evidence / 'pipe')
        self.browser.private.append(self.evidence / '004.txt')
        first = self.browser.browse(str(self.evidence))
        second = self.browser.browse(str(self.evidence), first['next_offset'])
        entries = first['entries'] + second['entries']
        self.assertEqual(len(first['entries']), 100)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_offset'])
        self.assertEqual(entries[0]['name'], 'Folder')
        self.assertEqual(len({entry['name'] for entry in entries}), 106)
        self.assertTrue({'link', 'pipe', '.secret', '004.txt'}.isdisjoint(entry['name'] for entry in entries))
        with self.assertRaises(OSError):
            self.browser.browse(str(self.evidence / 'link'))
        ancestor_link = self.root / 'alias'
        ancestor_link.symlink_to(self.evidence, target_is_directory=True)
        with self.assertRaises(OSError):
            self.browser.browse(str(ancestor_link / 'Folder'))
        with patch('forsic_plugin.onboarding.Path.home', return_value=ancestor_link / 'Folder'):
            with self.assertRaises(OSError):
                self.browser.browse()
        with patch('forsic_plugin.onboarding.Path.home', return_value=self.home):
            with self.assertRaises(PermissionError):
                self.browser.browse()

    def test_api_empty_path_browses_synthetic_home_without_writes(self):
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        (self.evidence / '.hidden').write_text('synthetic hidden')
        (self.evidence / 'link').symlink_to(self.home, target_is_directory=True)
        with patch.object(api, 'directory', return_value=self.catalog), TestClient(app) as client:
            for params in ({}, {'path': ''}):
                result = client.get('/api/plugins/forsic/evidence-browser', params=params)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['path'], str(self.root))
                self.assertEqual(result.json()['entries'],
                                 [{'name': self.evidence.name, 'path': str(self.evidence), 'kind': 'directory'}])
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())

    def test_picker_bounded_scan_and_safe_api_errors(self):
        self.browser.max_entries = 1
        (self.evidence / 'other.txt').write_text('synthetic')
        with self.assertRaises(ValueError):
            self.browser.browse(str(self.evidence))
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        with patch.object(api, 'directory', return_value=self.catalog), TestClient(app) as client:
            denied = client.get('/api/plugins/forsic/evidence-browser', params={'path': str(self.home)})
            self.assertEqual(denied.status_code, 403)
            self.assertNotIn(str(self.home), denied.text)
            missing = client.get('/api/plugins/forsic/evidence-browser', params={'path': str(self.root / 'missing')})
            self.assertEqual(missing.status_code, 400)
            self.assertNotIn(str(self.root), missing.text)
            self.assertEqual(client.get('/api/plugins/forsic/evidence-browser', params={'offset': -1}).status_code, 422)
            bad = client.post('/api/plugins/forsic/investigations', json={'request_id': str(uuid.uuid4()),
                'path': str(self.file), 'onboarding': {'purpose': 'invalid', 'environment': 'linux'}})
            self.assertEqual(bad.status_code, 422)

    def test_connection_checks_entry_only_without_reads_or_writes(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        with patch('forsic_plugin.onboarding.os.scandir', side_effect=AssertionError('no listing')), \
                patch('forsic_plugin.onboarding.os.read', side_effect=AssertionError('no content read')):
            for target, kind in ((self.evidence, 'directory'), (self.file, 'file')):
                self.assertEqual(self.browser.connection('  ' + str(target) + '  '),
                                 {'path': str(target), 'name': target.name, 'kind': kind,
                                  'access': 'readable', 'scope': 'entry_only'})
                self.assertEqual(self.browser.connection('/' + str(target))['path'], str(target))
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())

    def test_connection_rejects_unsafe_or_unavailable_entries(self):
        (self.evidence / 'link').symlink_to(self.file)
        alias = self.root / 'alias'
        alias.symlink_to(self.evidence, target_is_directory=True)
        pipe = self.evidence / 'pipe'
        os.mkfifo(pipe)
        for target in (self.evidence / 'link', alias / self.file.name, self.evidence / 'missing'):
            with self.assertRaises(OSError):
                self.browser.connection(str(target))
        with self.assertRaises(ValueError):
            self.browser.connection(str(pipe))
        for target in ('', ' ', None, 3, 'relative', '~/evidence', 'bad\x00path',
                       str(self.evidence / '..'), '/' + 'x' * 4096):
            with self.assertRaises(ValueError):
                self.browser.connection(target)
        for target in (self.home, self.home / 'settings', self.intake, self.root, Path('/etc'), Path('//etc')):
            with self.assertRaises(PermissionError):
                self.browser.connection(str(target))
        # Selecting a parent would expose protected descendants, even if absent.
        parent = self.evidence / 'parent'
        parent.mkdir()
        self.browser.private.append(parent / 'private')
        with self.assertRaises(PermissionError):
            self.browser.connection(str(parent))
        self.assertEqual(self.browser.browse(str(parent))['path'], str(parent))
        with patch('forsic_plugin.onboarding.os.open', side_effect=PermissionError('private diagnostic')):
            with self.assertRaises(PermissionError):
                self.browser.connection(str(self.file))

    def test_connection_api_and_report_body_contract_without_case_creation(self):
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        pipe = self.evidence / 'pipe'
        os.mkfifo(pipe)
        link = self.evidence / 'link'
        link.symlink_to(self.file)
        with patch.object(api, 'directory', return_value=self.catalog), \
                patch.object(self.catalog, 'create_conversation', return_value={'stub': True}) as create, \
                TestClient(app) as client:
            endpoint = '/api/plugins/forsic/evidence-connection'
            for target, kind in ((self.file, 'file'), (self.evidence, 'directory')):
                response = client.get(endpoint, params={'path': str(target)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'path': str(target), 'name': target.name,
                                                  'kind': kind, 'access': 'readable', 'scope': 'entry_only'})
            for target, status in ((self.evidence / 'missing', 400), (pipe, 400), (link, 400),
                                   (self.home, 403), (self.root, 403), (Path('/etc'), 403)):
                response = client.get(endpoint, params={'path': str(target)})
                self.assertEqual(response.status_code, status)
                self.assertNotIn(str(target), response.text)
            for params in ({}, {'path': ''}, {'path': 'x' * 4097}):
                self.assertEqual(client.get(endpoint, params=params).status_code, 422)
            with patch.object(EvidenceBrowser, 'connection', side_effect=PermissionError('private diagnostic')):
                response = client.get(endpoint, params={'path': str(self.file)})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('private diagnostic', response.text)
            create.assert_not_called()
            for purpose in ('report', 'timeline'):
                selected = {'purpose': purpose, 'environment': 'unknown', 'question': ''}
                request = uuid.uuid4()
                response = client.post('/api/plugins/forsic/investigations', json={
                    'request_id': str(request), 'path': str(self.file), 'onboarding': selected})
                self.assertEqual(response.status_code, 200)
                create.assert_called_with(request, case_id='', path=str(self.file), onboarding=selected)
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())

    def test_configured_sources_are_unverified_and_have_no_filesystem_or_case_access(self):
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        absent = self.evidence / 'not-created'
        with patch.object(api, 'directory', side_effect=AssertionError('no case access')), \
                patch('forsic_plugin.onboarding.os.open', side_effect=AssertionError('no filesystem access')), \
                patch.object(api, 'load_config') as config, TestClient(app) as client:
            for settings, expected in (({}, {'sources': []}),
                                       ({'evidence_directory': ''}, {'sources': []}),
                                       ({'evidence_directory': str(absent)},
                                        {'sources': [{'path': str(absent), 'name': absent.name}]}),
                                       ({'evidence_directory': '/' + str(absent)},
                                        {'sources': [{'path': str(absent), 'name': absent.name}]})):
                config.return_value = {'plugins': {'entries': {'forsic': {'settings': settings}}}}
                response = client.get('/api/plugins/forsic/evidence-sources')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), expected)
            for value in ({'secret': 'private diagnostic'}, [], 7, 'relative', '  ',
                          'bad\x00path', '/data/../private', '/' + 'x' * 4096):
                config.return_value = {'plugins': {'entries': {'forsic': {
                    'settings': {'evidence_directory': value}}}}}
                response = client.get('/api/plugins/forsic/evidence-sources')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'detail': '설정된 자료 위치를 확인해주세요.'})
        self.assertFalse(absent.exists())
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())


if __name__ == '__main__':
    unittest.main()
