"""Specialist input -> native handler -> one writer -> report; synthetic, offline."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

from unittest.mock import Mock

import test_investigation_context as fixtures
from forsic_plugin import register
from forsic_plugin.report_driven import host


class SpecialistContracts(unittest.TestCase):
    bind = fixtures.InvestigationContextTests.bind
    session = fixtures.InvestigationContextTests.session
    save_note = fixtures.InvestigationContextTests.save_note

    def call(self, tool, args):
        return json.loads(self.handlers[tool](args, session_id=self.sid))

    def setUp(self):
        fixtures.InvestigationContextTests.setUp(self)
        ctx = Mock()
        ctx.get_config.side_effect = lambda key: str(self.intake) if key == "intake_root" else None
        register(ctx)
        self.handlers = {c.kwargs["name"]: c.kwargs["handler"] for c in ctx.register_tool.call_args_list}
        self.schemas = {c.kwargs["name"]: c.kwargs["schema"] for c in ctx.register_tool.call_args_list}
        self.lines = ['접속 2026-10-01T23:59:00Z accepted', 'Oct  2 09:02:00 attempt']
        self.path = self.case.root / 'times.log'
        self.path.write_text('\n'.join(self.lines))
        self.original = self.path.read_bytes()
        self.read = self.call('forsic_read', {'path': 'times.log'})
        self.eid = self.read['evidence_id']

    def event(self, index=0):
        literal = ('2026-10-01T23:59:00Z', 'Oct  2 09:02:00')[index]
        return dict(description='Timestamp recorded in the log', evidence_ids=[self.eid],
                    time_source=dict(evidence_id=self.eid, field='log', pointer=f'/lines/{index}/text',
                                     literal=literal, byte_start=self.lines[index].encode().index(literal.encode())))

    def payload(self, **changes):
        value = dict(operation_id='time-question', question='What time is recorded?',
                     scope='Synthetic acquired log', answer='The log records an accepted connection; its actor is unknown.',
                     reason='Retain the observed log timestamp', priority='material', timeline=[self.event()],
                     judgment=dict(citation_refs=[self.read['presented_spans'][0]['citation_ref']],
                                   assertion_kind='interpretation', reasoning_summary='Acceptance is a log observation, not attribution.'))
        value.update(changes)
        return value

    def save(self, payload):
        out = self.call('forsic_question', payload)
        self.assertNotIn('error', out, out)
        qid = out['question'].get('question_id') or out['question']['id']
        return host.find(host.current(self.case), 'questions', qid)

    def test_native_log_timeline_reaches_current_note_and_both_report_formats(self):
        q = self.save(self.payload(timeline=[self.event(), self.event(1), self.event()]))
        out = self.call('forsic_note', {'action': 'get', 'note_id': q['id'].removeprefix('Q-')})
        rows = out['note']['timeline']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['display_time'], '2026-10-02 08:59:00 KST')
        self.assertEqual(rows[0]['raw_time'], '2026-10-01T23:59:00Z')
        self.assertFalse(rows[1]['comparable'])
        state = host.current(self.case); host.checked(state)
        for key in ('missions', 'tests', 'assessments'): self.assertEqual(state[key], [])
        self.assertEqual(len(state['timeline']), 2)
        report = self.call('forsic_reporting', {'action': 'render', 'allow_partial': True, 'question_ids': [q['id']]})
        self.assertNotIn('error', report, report)
        for filename in report['files']:
            path = Path(filename)
            if path.suffix == '.html': text = path.read_text()
            else:
                with ZipFile(path) as doc: text = doc.read('word/document.xml').decode()
            self.assertIn('2026-10-02T08:59:00+09:00', text)
            self.assertIn('Oct  2 09:02:00', text)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_timeline_correction_preserves_history_and_rejects_stale_cas(self):
        q = self.save(self.payload())
        source = deepcopy(self.case.event(self.eid))
        p = dict(operation_id='correct-time', question_id=q['id'], expected_revision=q['revision'],
                 reason='Select the second record; its timezone is unknown', timeline=[self.event(1)])
        revised = self.save(p)
        self.assertEqual(revised['revision'], q['revision'] + 1)
        self.assertEqual(revised['claim_refs'], q['claim_refs'])
        self.assertEqual(self.save(p), revised)
        before = host.current(self.case)
        self.assertIn('revision', self.call('forsic_question', {**p, 'operation_id': 'stale'})['error'].lower())
        self.assertEqual(before['meta']['content_revision'], host.current(self.case)['meta']['content_revision'])
        note = self.call('forsic_note', {'action': 'get', 'note_id': q['id'].removeprefix('Q-'),
                                       'include_history': True, 'limit': 10})
        self.assertEqual(note['note']['timeline'][0]['raw_time'], 'Oct  2 09:02:00')
        self.assertTrue(any(n['timeline'][0]['raw_time'] == '2026-10-01T23:59:00Z' for n in note['history']))
        self.assertEqual(self.case.event(self.eid), source)

    def test_bad_binding_rejects_new_question_and_claim_atomically_then_repairs(self):
        for change in ({'literal': '2026-10-02T23:59:00Z'}, {'byte_start': 3}, {'pointer': '/path'},
                       {'evidence_id': 'missing-source'}, {'byte_start': True}):
            with self.subTest(change=change):
                row = self.event(); row['time_source'].update(change)
                before = host.current(self.case)
                result = self.call('forsic_question', self.payload(timeline=[row]))
                self.assertIn('error', result)
                after = host.current(self.case)
                for key in ('questions', 'claims', 'timeline', 'observations'): self.assertEqual(after[key], before[key])
        self.save(self.payload())

    def test_failed_or_uncited_sources_are_not_accepted_as_timeline(self):
        failed = self.case.record('tool_result', dict(tool='forsic_read', error='unavailable',
                                                    lines=[{'text': self.lines[0]}]))
        for ids, eid in (([failed], failed), ([], self.eid), ([self.eid, 'invented'], self.eid)):
            row = self.event(); row['evidence_ids'] = ids; row['time_source']['evidence_id'] = eid
            before = host.current(self.case)
            out = self.call('forsic_question', self.payload(timeline=[row]))
            self.assertIn('error', out)
            self.assertEqual(before['meta']['content_revision'], host.current(self.case)['meta']['content_revision'])

    def test_filesystem_timestamp_requires_exact_retained_entry(self):
        eid = self.case.record('tool_result', dict(tool='forsic_image_files', action='list',
            entries=[{'path': '/first', 'mtime_epoch': 0}, {'path': '/second', 'mtime_epoch': 60}]))
        row = dict(description='File mtime metadata, not execution', evidence_ids=[eid],
                   time_source=dict(evidence_id=eid, field='mtime'))
        self.assertIn('entry_path', self.call('forsic_question', self.payload(timeline=[row]))['error'])
        row['time_source']['entry_path'] = '/second'
        q = self.save(self.payload(timeline=[row]))
        state = host.current(self.case); host.checked(state)
        tm = next(t for t in state['timeline'] if q['id'] in t['id'])
        obs = host.find(state, 'observations', tm['observation_ref']['id'])
        self.assertEqual(obs['field_pointer'], '/entries/1/mtime_epoch')
        self.assertEqual(tm['file_time_type'], 'mtime')
        self.assertEqual(tm['raw_values'], ['60'])

    def test_native_pin_and_admission_agree_on_required_time_origin(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from jsonschema import Draft202012Validator
        wire = sanitize_tool_schemas([{'type': 'function', 'function': self.schemas['forsic_question']}])[0]['function']['parameters']
        self.assertFalse(list(Draft202012Validator(wire).iter_errors(self.payload())))
        for row in ({'description': 'invented date', 'evidence_ids': [self.eid], 'time': '2026-10-02T00:00:00Z'},
                    {**self.event(), 'time_source': {'field': 'log', 'evidence_id': self.eid}}):
            p = self.payload(timeline=[row])
            self.assertTrue(list(Draft202012Validator(wire).iter_errors(p)))
            self.assertIn('error', self.call('forsic_question', p))


if __name__ == '__main__': unittest.main()
