"""Offline display projection: exact spans, clock domains, bounded reads and no writes."""
import json
import unittest
from unittest.mock import patch

from forsic_plugin import investigation_display as display
from forsic_plugin.report_driven.host import source_record, content_version
from forsic_plugin.report_driven.model_pages import select
import test_investigation_view as fixtures


class TimestampDisplayTests(unittest.TestCase):
    def project(self,data):
        source = source_record(dict(id='selected',data=data))
        return source, display.timestamp_projection(source,data)

    def test_exact_unicode_offsets_and_source_local_calendar_without_invented_zone(self):
        data = dict(tool='forsic_shell',stdout='한글: 2024-08-19 03:04:05 original\n2026-03-19 04:05:06 later')
        source,(observations,times,_) = self.project(data)
        self.assertEqual(len(times),2)
        self.assertEqual(times[0]['local_values'],['2024-08-19T03:04:05'])
        self.assertFalse(times[0]['comparable'])
        self.assertEqual(times[0]['normalized_values'],[])
        self.assertEqual(times[0]['clock_domain'],'source-local:selected')
        self.assertEqual(times[0]['temporal_context']['coordinate_basis'],'source_local_calendar')
        self.assertEqual(times[0]['temporal_context']['precision'],'second')
        self.assertEqual(times[0]['excerpt_boundary'],'earliest')
        self.assertEqual(times[1]['excerpt_boundary'],'latest')
        for ob in observations:
            raw = select(data,ob['field_pointer']).encode()
            self.assertEqual(raw[ob['byte_start']:ob['byte_end']].decode(),ob['literal'])
            self.assertEqual(ob['source_ref']['version'],source['version'])
            canonical = {k:v for k,v in ob.items() if not k.startswith('display_')}
            self.assertEqual(ob['version'],content_version(canonical))

    def test_explicit_offset_is_instant_metadata_is_separate_and_date_is_day(self):
        _,(_,rows,_) = self.project(dict(tool='forsic_read',text='Modify: 2026-03-19 03:04:05 +0900\n'
            'Change: 2026-03-20 04:05:06Z\n2026-03-21 record\n2026-02-30 invalid\nOct 1 03:00 unknown year'))
        self.assertEqual(len(rows),3)
        metadata = [row for row in rows if row['time_kind']=='file_metadata']
        self.assertEqual({row['file_time_type'] for row in metadata},{'mtime','ctime'})
        self.assertIn('2026-03-18T18:04:05Z',[row['normalized_values'][0] for row in metadata])
        date = next(row for row in rows if row['precision']=='day')
        self.assertEqual(date['local_values'],['2026-03-21'])
        self.assertEqual(date['normalized_values'],[])
        self.assertTrue(all(not row['inferred'] for row in rows))

    def test_commands_acquisition_clocks_and_unselected_payloads_are_not_timestamps(self):
        _,(observations,rows,_) = self.project(dict(tool='forsic_shell',
            command='echo 2000-01-01',started_at='2001-01-01',lookup_at='2002-01-01',
            input=dict(text='2003-01-01'),stdout='no clocks',
            metadata=dict(collected_at='2004-01-01',mtime='2005-01-01')))
        self.assertEqual(len(rows),1)
        self.assertEqual(observations[0]['literal'],'2005-01-01')
        self.assertEqual(rows[0]['time_kind'],'file_metadata')

    def test_scan_and_row_limits_are_deterministic(self):
        data = dict(tool='forsic_shell',stdout='2020-01-01\n'+'x'*500+'\n2026-01-01')
        with patch.object(display,'MAX_FIELD_BYTES',64):
            _,(obs,rows,coverage) = self.project(data)
        self.assertTrue(coverage['scan_truncated'])
        self.assertEqual([ob['literal'] for ob in obs],['2020-01-01'])
        self.assertEqual(rows[0]['excerpt_boundary'],'earliest_latest')
        source = source_record(dict(id='selected',data=data))
        self.assertEqual(display.timestamp_projection(source,data,max_rows=0)[0],[])

    def test_display_preserves_canonical_identity_and_uses_matching_purpose(self):
        data = dict(tool='forsic_shell',stdout='selected returned literal')
        source = source_record(dict(id='selected',data=data)); before = dict(source)
        result = display.source_display(source,data,dict(reason='Inspect retained command history',
            arguments=dict(command='cat command.log')))
        self.assertEqual(result['display_title'],'Inspect retained command history')
        self.assertIn('selected returned literal',result['display_summary'])
        self.assertEqual(source,before)

    def test_only_explicit_numeric_epoch_metadata_fields_normalize_without_byte_spans(self):
        data = dict(tool='forsic_list',entries=[dict(mtime_epoch=0,ctime_epoch=86400.25,
            atime_epoch=-1,other_number=100,mtime=10,ctime_epoch_ms=1234567890)])
        _,(observations,rows,_) = self.project(data)
        self.assertEqual(len(rows),3)
        self.assertEqual({row['file_time_type'] for row in rows},{'mtime','ctime','atime'})
        self.assertTrue(all(row['comparable'] for row in rows))
        self.assertIn('1970-01-02T00:00:00.250000Z',[row['normalized_values'][0] for row in rows])
        for observation in observations:
            self.assertNotIn('byte_start',observation)
            self.assertNotIn('byte_end',observation)
            self.assertEqual(observation['coordinate_basis'],'retained numeric scalar field')
            value = select(data,observation['field_pointer'])
            self.assertEqual(observation['literal'],str(value))
            self.assertEqual(observation['value'],value)
        _,(observations,rows,_) = self.project(dict(tool='forsic_list',mtime_epoch=float('inf')))
        self.assertEqual((observations,rows),([],[]))


class IntegratedDisplayTests(unittest.TestCase):
    setUp = fixtures.InvestigationViewTests.setUp
    append = fixtures.InvestigationViewTests.append
    project = fixtures.InvestigationViewTests.project
    changes = fixtures.InvestigationViewTests.changes

    def test_saved_answers_and_unselected_tool_dates_cannot_supply_time_rows(self):
        self.append('tool_result',dict(tool='forsic_read',text='2000-01-01 unselected'))
        note = dict(self.note,answer='The incident began 2001-01-01')
        self.append('note',note)
        before = self.case.db.read_bytes()
        result = self.project()
        self.assertEqual(result['timeline'],[])
        self.assertEqual(self.case.db.read_bytes(),before)

    def test_selected_retained_dates_supply_exact_rows_without_analyst_assertions(self):
        sid = self.append('tool_result',dict(tool='forsic_shell',stdout='한글 2024-08-19 03:04:05\n2026-03-19 04:05:06'))
        self.append('note',dict(self.note,evidence_ids=[sid]))
        before = self.case.db.read_bytes()
        result = self.project()
        self.assertEqual(result['counts']['timeline'],2)
        self.assertEqual(result['counts']['observations'],2)
        self.assertEqual(self.case.db.read_bytes(),before)
        for ob in result['observations']:
            page = __import__('forsic_plugin.investigation_view',fromlist=['selected_source']).selected_source(
                self.resolved,sid,ob['source_ref']['version'],pointer=ob['field_pointer'],
                offset=ob['byte_start'],limit=ob['byte_end']-ob['byte_start'])
            self.assertEqual(page['text'],ob['literal'])

    def test_numeric_time_observation_opens_exact_scalar_source_page(self):
        data = dict(tool='forsic_list',entries=[dict(mtime_epoch=86400.25)])
        sid = self.append('tool_result',data)
        self.append('note',dict(self.note,evidence_ids=[sid]))
        result = self.project(); ob = result['observations'][0]
        page = __import__('forsic_plugin.investigation_view',fromlist=['selected_source']).selected_source(
            self.resolved,sid,ob['source_ref']['version'],pointer=ob['field_pointer'])
        self.assertEqual(page['value'],86400.25)
        self.assertEqual(result['timeline'][0]['time_kind'],'file_metadata')
        self.assertNotIn('byte_start',ob)

    def test_existing_exact_observation_is_reused_for_unsaved_observed_timeline(self):
        data = dict(tool='forsic_shell',stdout='2026-03-19 03:04:05')
        sid = self.append('tool_result',data)
        source = source_record(dict(id=sid,data=data))
        observation = dict(id='O-selected',version='saved-version',
            source_ref=dict(kind='source',id=sid,version=source['version']),
            statement='Existing selected literal',field_pointer='/stdout',literal=data['stdout'],
            byte_start=0,byte_end=len(data['stdout']))
        claim = dict(id='F-selected',version='saved',status='adopted',
            source_refs=[dict(kind='observation',id=observation['id'],version=observation['version'])])
        self.append('report_change',self.changes(dict(observations=[observation],claims=[claim])))
        note = dict(self.note,evidence_ids=[sid],investigation=dict(self.note['investigation'],
            claim_refs=[dict(id=claim['id'],version=content_version(claim))]))
        self.append('note',note)
        result = self.project()
        self.assertEqual(result['counts']['observations'],1)
        self.assertEqual(result['counts']['timeline'],1)
        self.assertEqual(result['timeline'][0]['observation_ref'],dict(kind='observation',
            id='O-selected',version=content_version(observation)))

    def test_review_questions_are_advice_counted_separately_from_main_notes(self):
        from forsic_plugin import independent_review as review
        from forsic_plugin.independent_review_state import _presented
        identity = dict(case_id='synthetic',case_manifest=str(self.case.manifest),goal_id='older',conversation_id='session')
        request = dict(schema_version=2,request_id='review-synthetic',session_id='session',identity=identity,created_ns=1,
            messages=[dict(role='user',content=json.dumps(dict(sources=[dict(source_id=self.source_id,
                fields=[dict(pointer='/lines/0/text',value='Oct 1 03:00 synthetic activity')])])) )])
        advice = json.dumps(dict(findings=[dict(source_id=self.source_id,pointer='/lines/0/text',
            quote='Oct 1 03:00',question='What does this clock establish?',why='Year absent',next_check='Find year')]))
        finding = review._parse(advice,_presented(request),'review-synthetic')[0]
        request_path = self.case.output/'independent-review-synthetic-request.json'
        request_path.write_text(json.dumps(request))
        request_path.with_name('independent-review-synthetic-response.json').write_text(json.dumps(
            dict(request_id='review-synthetic',finish_reason='stop',raw_advice=advice)))
        note = dict(self.note,goal_id='older',investigation=dict(self.note['investigation'],review_responses=[dict(
            review_id='review-synthetic',finding_id=finding['finding_id'],disposition='scope_limited',
            reason='Year is unavailable in this retained source.', limitation='A year requires another source.',
            citation_refs=[],citations=[],question_definition_version=self.note['definition_version'])]))
        self.append('note',note)
        result = self.project()
        self.assertEqual(result['counts']['questions'],1)
        self.assertEqual(result['counts']['review_questions'],1)
        question = result['review_questions'][0]
        self.assertEqual(question['role'],'independent_advice_not_source_evidence')
        self.assertEqual(question['disposition'],'scope_limited')
        self.assertEqual(question['question_id'],'Q-n')
        self.assertFalse(question['current_goal'])
        self.assertEqual(question['currentness'],'not_revalidated')
        self.assertEqual(question['session_id'],'session')
        self.assertEqual(result['review_question_coverage']['current_goal_questions'],0)
        self.assertEqual(result['review_question_coverage']['historical_goal_questions'],1)
        self.resolved['goal_id'] = 'older'
        current = self.project()
        self.assertTrue(current['review_questions'][0]['current_goal'])
        self.assertEqual(current['review_question_coverage']['current_goal_questions'],1)
        with patch.object(fixtures.view,'MAX_REVIEW_RECEIPTS',0):
            bounded = self.project()
        self.assertEqual(bounded['counts']['review_questions'],0)
        self.assertEqual(bounded['omitted_counts']['review_receipts'],1)
        self.assertTrue(bounded['partial'])


if __name__ == '__main__':
    unittest.main()
