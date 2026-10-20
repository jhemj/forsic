"""Actual protected-artifact helper boundaries with synthetic native scan receipts."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_sensitive_integration as native_fixture
from forsic_plugin import sensitive_policy as policy
from forsic_plugin.evidence_collection import CollectionError, list_collection
from forsic_plugin.evidence_collection_export import create_export, export_download, export_status, verify_collection


class SensitiveCollectionTests(unittest.TestCase):
    setUp = native_fixture.SensitiveIntegrationTests.setUp
    call = native_fixture.SensitiveIntegrationTests.call
    target = native_fixture.SensitiveIntegrationTests.target
    args = native_fixture.SensitiveIntegrationTests.args
    backend = native_fixture.SensitiveIntegrationTests.backend
    scan = native_fixture.SensitiveIntegrationTests.scan
    secret = native_fixture.SensitiveIntegrationTests.secret

    def adopt(self, *, neighbor=False):
        content = 'NEIGHBOR_PRIVATE_CANARY ' + self.secret + ' UNSELECTED_PRIVATE_CANARY\n' if neighbor else self.secret + '\n'
        args = {'action': 'sensitive_scan', 'targets': [self.target(content=content)],
                'profiles': ['pii_kr'], 'mode': 'scope_count'}
        def backend(command, *pos, **kwargs):
            result = self.backend(command, *pos, **kwargs)
            if neighbor:
                for i, value in enumerate(command):
                    if value == '--mount':
                        mount = dict(x.split('=', 1) for x in next(csv.reader([command[i + 1]])) if '=' in x)
                        if mount['dst'] == '/private':
                            private = Path(mount['src'])
                saved = json.loads((private / 'result.json').read_text())
                start = len(content[:content.index(self.secret)].encode())
                saved['findings'][0].update(match_byte_start=start, match_byte_end=start + len(self.secret.encode()))
                policy._write(private / 'result.json', policy._json(saved), replace=True)
            return result
        result, runner = self.scan(args, backend=backend)
        self.assertNotIn('error', result, result)
        self.assertEqual(runner.call_count, 1)
        lookup = self.call('forsic_reporting', {'action': 'source', 'source_id': result['source_ref']['id'],
            'source_version': result['source_ref']['version'], 'pointer': '/findings/0/masked_excerpt'})
        saved = self.call('forsic_question', {'operation_id': 'sensitive-collection-question',
            'question': 'What candidate is retained?', 'scope': 'Selected synthetic detector result',
            'answer': 'One masked candidate; real person and incident remain unassessed.',
            'reason': 'Retain only actual detector observation', 'priority': 'contextual',
            'judgment': {'citation_refs': [lookup['citation_ref']], 'assertion_kind': 'fact',
                         'reasoning_summary': 'Only the selected masked candidate is asserted.'}})
        self.assertNotIn('error', saved, saved)
        self.context = {'case_id': self.case.config['case_id'], 'profile_id': 'synthetic-profile',
            'session_id': 'shell-native', 'conversation_id': 'shell-conversation',
            'session_ids': ['shell-native', 'shell-conversation']}
        return result

    def export(self, *, originals=False, key='sensitive-collection-one', admin=False):
        page = list_collection(self.case, context=self.context)
        return create_export(self.case, context=self.context, source_snapshot_id=page['source_snapshot_id'],
            selection='all_eligible', idempotency_key=key, include_sensitive_originals=originals, local_admin=admin)

    def payload(self, metadata, *, admin=False):
        buffer, status = export_download(self.case, metadata['export_id'], context=self.context, local_admin=admin)
        with zipfile.ZipFile(buffer) as archive:
            return b''.join(archive.read(name) for name in archive.namelist()), archive.namelist()

    def test_default_list_preview_zip_masked_no_private_hydration(self):
        self.adopt(neighbor=True)
        with patch('forsic_plugin.sensitive_policy.report_appendix', side_effect=AssertionError('private hydration forbidden')), \
             patch.object(self.case, 'path', side_effect=AssertionError('original recollection forbidden')):
            page = list_collection(self.case, context=self.context)
            metadata = self.export()
            payload, names = self.payload(metadata)
        self.assertEqual(page['counts']['restricted'], 1)
        self.assertEqual(page['items'][0]['kind'], 'sensitive_detection')
        self.assertIsNone(page['items'][0]['original_locator'])
        self.assertNotIn(self.secret, json.dumps(page))
        self.assertIn(b'<PII:D00001>', payload)
        for forbidden in (self.secret.encode(), b'NEIGHBOR_PRIVATE_CANARY', b'UNSELECTED_PRIVATE_CANARY', b'value_hmac', b'hmac.key', b'stderr.bin'):
            self.assertNotIn(forbidden, payload)
        self.assertFalse(any(name.startswith('restricted/') for name in names))

    def test_explicit_local_admin_verified_match_only_originals_and_get_guard(self):
        self.adopt(neighbor=True)
        with self.assertRaises(CollectionError) as raised:
            self.export(originals=True)
        self.assertEqual(raised.exception.status_code, 403)
        metadata = self.export(originals=True, admin=True)
        payload, names = self.payload(metadata, admin=True)
        self.assertIn(self.secret.encode(), payload)
        self.assertNotIn(b'NEIGHBOR_PRIVATE_CANARY', payload)
        self.assertNotIn(b'UNSELECTED_PRIVATE_CANARY', payload)
        self.assertNotIn(b'value_hmac', payload)
        self.assertNotIn(b'hmac.key', payload)
        self.assertTrue(metadata['contains_sensitive_originals'])
        self.assertTrue(metadata['local_only'])
        self.assertNotIn(self.secret, json.dumps(metadata))
        for action in (export_status, export_download):
            with self.assertRaises(CollectionError) as raised:
                action(self.case, metadata['export_id'], context=self.context)
            self.assertEqual(raised.exception.status_code, 403)
        buffer, _ = export_download(self.case, metadata['export_id'], context=self.context, local_admin=True)
        self.assertEqual(verify_collection(buffer)['verified_items'], 1)

    def test_private_result_or_staged_input_hash_tamper_fails_no_masked_fallback(self):
        result = self.adopt()
        base = policy._base(self.case) / result['scan_id']
        for number, path in enumerate((base / 'output' / 'result.json', base / 'inputs' / 'F0001')):
            original = path.read_bytes()
            policy._write(path, original + b' ', replace=True)
            with self.assertRaises(CollectionError):
                self.export(originals=True, admin=True, key='tampered-private-%d' % number)
            self.assertFalse(list((self.case.output / '.evidence-collection').glob('*/ready.json')))
            policy._write(path, original, replace=True)

    def test_changed_input_source_version_fails_even_if_staged_bytes_intact(self):
        result = self.adopt()
        job = policy._base(self.case) / result['scan_id']
        manifest = json.loads((job / 'manifest.json').read_text())
        source_id = manifest['identity']['inputs'][0]['source_ref']['id']
        event = self.case.event(source_id)
        event['data']['synthetic_tamper'] = True
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), source_id))
        with self.assertRaisesRegex(CollectionError, 'source version'):
            self.export(originals=True, admin=True)


if __name__ == '__main__':
    unittest.main()
