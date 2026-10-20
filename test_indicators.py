"""Synthetic local IOC register acceptance; no network, model or private case."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin import indicators


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'evidence').mkdir()
        manifest = root / 'case.json'
        manifest.write_text(json.dumps(dict(case_id='SYN-indicators', synthetic=True,
            evidence_root=str(root / 'evidence'), output_root=str(root / 'results'))))
        self.case = Case(manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, text, tool='forsic_read', **data):
        return self.case.record('tool_result', {'tool': tool, 'text': text, **data})

    def save(self, kind, value, text=None, **args):
        text = value if text is None else text
        source = self.source(text)
        ref = dict(source_id=source, pointer='/text', literal=value,
                   byte_start=len(text[:text.index(value)].encode()))
        return indicators.invoke(self.case, dict(action='upsert', type=kind,
            value=value, source_refs=[ref], **args))['indicator']

    def test_canonical_identity_merges_sources_and_preserves_raw_values(self):
        first = self.save('domain', 'EXAMPLE.COM.')
        second = self.save('domain', 'example.com', revision=first['revision'])
        self.assertEqual(first['indicator_id'], second['indicator_id'])
        self.assertEqual(second['canonical_value'], 'example.com')
        self.assertEqual(second['raw_values'], ['EXAMPLE.COM.', 'example.com'])
        self.assertEqual(len(second['source_refs']), 2)
        self.assertEqual(len(indicators.current(self.case)), 1)
        same = indicators.invoke(self.case, dict(action='upsert', type='domain', value='example.com',
            revision=2, source_refs=[second['source_refs'][1]]))
        self.assertTrue(same['reused'])
        self.assertEqual(same['indicator']['revision'], 2)

    def test_local_types_and_conservative_identity(self):
        self.assertEqual(indicators.canonical('ip', '2001:0db8:0:0::1'), '2001:db8::1')
        self.assertEqual(indicators.canonical('ip', '10.1.2.3'), '10.1.2.3')
        self.assertEqual(indicators.canonical('domain', 'DB01.INTERNAL.'), 'db01.internal')
        self.assertEqual(indicators.canonical('url', 'HTTPS://EXAMPLE.COM/A?x=B'), 'https://example.com/A?x=B')
        self.assertNotEqual(indicators.canonical('path', '/A'), indicators.canonical('path', '/a'))
        self.assertNotEqual(indicators.canonical('account', 'User'), indicators.canonical('account', 'user'))
        self.assertEqual(indicators.canonical('hash', 'A' * 64), 'a' * 64)
        for kind, value in [('ip', 'bad'), ('url', 'javascript:alert(1)'), ('domain', 'a/b'),
                            ('domain', 'a..b'), ('hash', 'g' * 64), ('path', 'a\nb')]:
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                indicators.canonical(kind, value)

    def test_utf8_exact_provenance_and_invented_or_wrong_value_rejected(self):
        row = self.save('ip', '10.0.0.1', '한글 IP=10.0.0.1 end')
        ref = row['source_refs'][0]
        self.assertEqual(ref['byte_start'], len('한글 IP='.encode()))
        self.assertEqual(ref['source_version'], indicators.digest(self.case.event(ref['source_id'])['data']))
        base = dict(action='upsert', type='ip', value='10.0.0.1', revision=1)
        for change in ({'byte_start': 1}, {'literal': '10.0.0.2'}, {'source_version': 'invented'},
                       {'pointer': '/missing'}, {'source_id': 'invented'}):
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                indicators.invoke(self.case, {**base, 'source_refs': [{**ref, **change}]})
        with self.assertRaisesRegex(ValueError, 'canonical indicator'):
            indicators.invoke(self.case, {**base, 'value': '10.0.0.2', 'source_refs': [ref]})

    def test_failed_sources_and_negative_array_pointer_rejected(self):
        failed = self.source('10.0.0.1', error='read failed')
        wrong = self.case.record('note', {'text': '10.0.0.1'})
        for source in (failed, wrong):
            with self.assertRaises(ValueError):
                indicators.invoke(self.case, dict(action='upsert', type='ip', value='10.0.0.1',
                    source_refs=[dict(source_id=source, pointer='/text', literal='10.0.0.1', byte_start=0)]))
        source = self.case.record('tool_result', {'tool': 'forsic_read', 'lines': [{'text': '10.0.0.1'}]})
        with self.assertRaises(ValueError):
            indicators.invoke(self.case, dict(action='upsert', type='ip', value='10.0.0.1',
                source_refs=[dict(source_id=source, pointer='/lines/-1/text', literal='10.0.0.1', byte_start=0)]))

    def test_hash_provenance_slice_never_becomes_whole_file(self):
        value = 'a' * 64
        for tool, field, data, expected in [
                ('forsic_read_bytes', 'sha256_slice', {}, None),
                ('forsic_image_files', 'sha256', {}, None),
                ('forsic_hash', 'sha256', {}, 'whole_file_hash'),
                ('forsic_image_files', 'sha256', {'complete_file': True}, 'whole_file_hash'),
                ('forsic_read', 'text', {}, 'recorded_hash')]:
            with self.subTest(tool=tool, field=field, data=data):
                source = self.case.record('tool_result', {'tool': tool, field: value, **data})
                previous = indicators.current(self.case)
                args = dict(action='upsert', type='hash', value=value,
                    source_refs=[dict(source_id=source, pointer='/' + field, literal=value, byte_start=0)])
                if previous:
                    args['revision'] = previous[0]['revision']
                if expected is None:
                    with self.assertRaises(ValueError):
                        indicators.invoke(self.case, args)
                else:
                    row = indicators.invoke(self.case, args)['indicator']
                    self.assertEqual(row['source_refs'][-1]['source_kind'], expected)

    def test_search_query_echo_and_missing_path_are_not_observations(self):
        query = self.case.record('tool_result', {'tool': 'forsic_search',
            'search_text': '10.0.0.1', 'matches': [], 'coverage_complete': True})
        with self.assertRaisesRegex(ValueError, 'Query inputs'):
            indicators.invoke(self.case, dict(action='upsert', type='ip', value='10.0.0.1',
                source_refs=[dict(source_id=query, pointer='/search_text', literal='10.0.0.1', byte_start=0)]))
        missing = self.case.record('tool_result', {'tool': 'forsic_image_files',
            'file_path': '/missing', 'outcome': 'not_found', 'error': 'not found'})
        with self.assertRaisesRegex(ValueError, 'missing or unavailable'):
            indicators.invoke(self.case, dict(action='upsert', type='path', value='/missing',
                source_refs=[dict(source_id=missing, pointer='/file_path', literal='/missing', byte_start=0)]))

    def test_status_changes_history_revision_and_no_automatic_intel_promotion(self):
        first = self.save('domain', 'evil.test')
        source = self.source('evil.test', tool='forsic_intel', source_kind='external_intelligence',
                             report={'malicious': 99})
        args = dict(action='upsert', type='domain', value='evil.test', revision=1,
            source_refs=[dict(source_id=source, pointer='/text', literal='evil.test', byte_start=0)])
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            row = indicators.invoke(self.case, args)['indicator']
        self.assertEqual(row['status'], 'observed')
        self.assertEqual(row['source_refs'][-1]['source_kind'], 'external_reference')
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            indicators.invoke(self.case, args)
        args['revision'] = row['revision']
        with self.assertRaisesRegex(ValueError, 'summary'):
            indicators.invoke(self.case, {**args, 'status': 'suspicious'})
        third = indicators.invoke(self.case, {**args, 'status': 'suspicious',
            'summary': '외부 평판이 있으나 로컬 실행은 미확인', 'limitations': ['실행 미확인']})['indicator']
        history = indicators.invoke(self.case, {'action': 'get', 'indicator_id': first['indicator_id']})
        self.assertEqual([item['revision'] for item in history['history']], [3, 2, 1])
        self.assertEqual(history['indicator'], third)
        withdrawn = indicators.invoke(self.case, {**args, 'revision': 3, 'status': 'withdrawn',
            'summary': '정정: 조사 범위와 무관함'})['indicator']
        self.assertEqual(len(withdrawn['source_refs']), 2)

    def test_paged_filter_and_exports_preserve_all_sources_and_prior_versions(self):
        first = self.save('ip', '10.0.0.1')
        self.save('domain', 'corp.internal')
        self.save('account', '=dangerous')
        page = indicators.invoke(self.case, {'action': 'list', 'limit': 2})
        self.assertEqual(page['total'], 3)
        self.assertEqual(page['next_offset'], 2)
        self.assertEqual(len(indicators.invoke(self.case, {'action': 'list', 'offset': 2})['indicators']), 1)
        self.assertEqual(indicators.invoke(self.case, {'action': 'list', 'type': 'ip'})['indicators'], [first])
        result = indicators.invoke(self.case, {'action': 'export'})
        path = Path(result['path'])
        original = path.read_bytes()
        self.assertEqual(path.parent, self.case.output)
        self.assertEqual(hashlib.sha256(original).hexdigest(), result['sha256'])
        self.assertEqual(json.loads(original)['indicators'], indicators.current(self.case))
        self.assertEqual(indicators.invoke(self.case, {'action': 'export'})['path'], str(path))
        self.save('ip', '10.0.0.1', revision=1, status='undetermined', summary='자료 부족')
        self.assertNotEqual(indicators.invoke(self.case, {'action': 'export'})['path'], str(path))
        self.assertEqual(path.read_bytes(), original)
        csv_result = indicators.invoke(self.case, {'action': 'export', 'format': 'csv'})
        with open(csv_result['path'], encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(next(row for row in rows if row['type'] == 'account')['canonical_value'], "'=dangerous")
        self.assertTrue(all(json.loads(row['source_refs']) for row in rows))

    def test_modified_and_symlink_exports_rejected(self):
        self.save('ip', '10.0.0.1')
        result = indicators.invoke(self.case, {'action': 'export'})
        path = Path(result['path'])
        original = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            indicators.invoke(self.case, {'action': 'export'})
        path.unlink()
        other = self.case.output / 'other.json'
        other.write_bytes(original)
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'changed'):
            indicators.invoke(self.case, {'action': 'export'})

    def test_download_serialization_is_passive_and_filters_match_list(self):
        first = self.save('ip', '10.0.0.1')
        self.save('domain', 'corp.internal')
        before_events = self.case.events(limit=1000)
        before_files = sorted(self.case.output.iterdir())
        payload = indicators.export_content(self.case, filters={'type': 'ip'})
        self.assertEqual(json.loads(payload)['indicators'], [first])
        self.assertTrue(indicators.export_content(self.case, 'csv').startswith(b'\xef\xbb\xbf'))
        self.assertEqual(self.case.events(limit=1000), before_events)
        self.assertEqual(sorted(self.case.output.iterdir()), before_files)


if __name__ == '__main__':
    unittest.main()
