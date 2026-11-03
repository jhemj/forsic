"""Fixed v2 authoring behavior; synthetic ledger only, no model or network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_report_driven import fixture, worked
from forsic_plugin.notes import note
from forsic_plugin.evidence import Case
from forsic_plugin.report_driven.host import current, invoke, checked
from forsic_plugin.report_driven.narrative import prepare, status, project_body
from forsic_plugin.report_driven.template_contract import GROUPS, WRITING_ORDER, REVIEW_CHECKS
from forsic_plugin.report_driven.views import compile_views, bundles

CONTENT = {
    'report_identity': [['미확인: 호스트 기록 미제공', '미확인: 주소 기록 미제공', '미확인: 이미지 채증 기록 미제공']],
    'overall_judgment': [['요청·생성 기록은 있으나 같은 작업인지 미확인입니다. 공통 식별이 없고 승인·실행 성공은 조사 범위 밖입니다.']],
    'major_findings': [
        ['시점 미확인', '최초 시각 미확인\n- 한계: 절대 시각을 확인할 공통 기준 미제공'],
        ['진입 경로 미확인', '요청 기록 존재\n- 한계: 비인가 진입 여부 미확인'],
        ['작업 연관 가능성 (추정)', '이름이 같은 작업의 연관 가능성 추정\n- 근거: 요청·프로세스 생성 기록의 이름 유사\n- 한계: 공통 식별·최종 실행 성공 미확인'],
        ['개인정보 내용은 검토 범위 밖', '보유·노출·반출 미확인'],
        ['외부 전송 기록은 선택 자료에 없음', '전송 부재로 일반화할 수 없음']],
    'event_overview': [['요청 기록만으로 유입 경로 판단 불가'], ['프로세스 생성은 확인되나 요청과 연결 미확인'], ['정보 접촉·전송은 선택 범위 밖']],
    'information_impact': [['보유 내용 미검토', '보유 여부 미확인'], ['외부 노출 경로 미검토', '노출 여부 미확인'], ['실제 전송 자료 미검토', '반출 여부 미확인']],
    'key_evidence': [['요청과 생성 기록의 이름 유사', '요청 기록과 생성 기록 각각 존재', '공통 작업 여부·승인·최종 실행 성공 미입증']],
    'synthesis': [['같은 이름의 관련 활동일 수 있으나 공통 식별이 없어 연결 유보'], ['서로 다른 작업일 수 있으며 공통 식별 추가 확보 때 재검토']],
    'timeline': [['시각 미확정', '요청 및 프로세스 생성 기록', '기록 순서를 인과 또는 최초 침해 시각으로 승격하지 않음']],
    'undated_events': [['두 기록의 절대 시각과 사건 간 시간차는 이 자료에서 미확정']],
    'scope': [['현재 판단은 선택한 요청·프로세스 기록의 연결에 한정되며 승인 여부·최종 성공·전체 시스템 상태는 포함하지 않음']],
}


class ReportNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, self.reads, self.note = fixture(self.tmp.name)
        question = worked(self.case)['questions'][0]
        invoke(self.case, {'action': 'question', 'payload': {
            'operation_id': 'editorial-fixture-assessment', 'question_id': question['id'],
            'expected_revision': question['revision'], 'assessment': 'undetermined',
            'reason': 'Explicitly retain the synthetic uncertain answer before editorial writing'}})

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, group, packet, *, unavailable=False, keys=None):
        _, _, nodes, columns, _ = next(contract for contract in GROUPS if contract[0] == group)
        nodes = nodes or (group + '.record1',)
        if keys is None:
            keys = [row['key'] for row in packet['items'] if row['kind'] in ('question', 'claim', 'gap')]
        return {'entries': [dict(node_id=node, cells=deepcopy(CONTENT[group][index]), basis_keys=keys,
                    status='unavailable' if unavailable else 'saved',
                    **({'unavailable_reason': '선택된 두 기록의 범위만 검토했으며 해당 사실을 확인할 자료가 없습니다.'}
                       if unavailable else {})) for index, node in enumerate(nodes)]}

    def write(self, group, *, unavailable=False, keys=None, **changes):
        packet = invoke(self.case, {'action': 'compose', 'section': group})
        request = dict(action='compose', section=group, question_ids=packet['question_ids'],
                       edit_ref=packet['save_contract']['edit_ref'],
                       operation_id=group + '-' + str(packet['revision']),
                       draft=self.draft(group, packet, unavailable=unavailable, keys=keys))
        request.update(changes)
        return invoke(self.case, request), request

    def review(self, *, blocking=False, omit_node=False, unresolved=False):
        packet = invoke(self.case, {'action': 'compose', 'section': 'review'})
        nodes = packet['reviewed_node_ids']
        review = {'summary': '각 항목과 선택 자료·한계를 대조한 합성 독자 검토입니다.',
            'reviewed_node_ids': nodes[:-1] if omit_node else nodes,
            'checks': [{'check': key, 'result': 'unresolved' if unresolved and key == 'evidence_support' else 'satisfied',
                        'reason': '선택한 기록이 입증하는 범위와 미확인 연결을 대조했습니다.'} for key in REVIEW_CHECKS],
            'comparisons': [{'node_ids': [nodes[0]], 'basis_keys': ['Q1'],
                'statement': '자료 시각과 연결의 미확정을 남긴 주요 결과입니다.',
                'source_support': 'Q1의 요청·생성 관측은 각각 존재하지만 같은 작업임을 입증하지 않습니다.',
                'limits': '공통 식별·절대 시각·승인·최종 실행 성공은 미확인입니다.',
                'assessment': '결론과 한계가 같은 범위로 표현되어 유지합니다.'}],
            'findings': [{'finding_id': 'scope-1', 'node_ids': [nodes[0]], 'disposition': 'unconfirmed',
                         'reason': '실제 사건 연결은 미확인으로 본문에 남겼습니다.', 'blocking': blocking}]}
        request = {'action': 'compose', 'section': 'review', 'question_ids': packet['question_ids'],
                   'operation_id': 'review-' + str(packet['revision']),
                   'edit_ref': packet['save_contract']['edit_ref'], 'review': review}
        return invoke(self.case, request), request

    def finish(self, *, unavailable=False):
        for group in WRITING_ORDER:
            result, _ = self.write(group, unavailable=unavailable)
        self.assertFalse(result['ready'])
        self.assertEqual(result['review_status'], 'required')
        result, _ = self.review()
        self.assertTrue(result['ready'])
        return result

    def test_native_render_requests_authored_prose_not_blank_template(self):
        before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('No hidden model call')):
            result = invoke(self.case, {'action': 'render'})
        self.assertFalse(result['rendered'])
        self.assertEqual(result['status'], 'authoring_required')
        self.assertEqual(result['section'], 'major_findings')
        self.assertEqual(before, self.case.db.read_bytes())
        self.assertFalse(list(self.case.output.glob('report-bundle-*')))
        self.assertLess(len(result['save_contract']['edit_ref']), 60)
        self.assertNotIn('expected_revision', result['save_contract'])
        self.assertNotIn('basis_id', result['save_contract'])

    def test_exact_source_choices_do_not_inherit_question_wide_citations(self):
        from forsic_plugin.report_driven.narrative import material, basis
        state = current(self.case)
        items = material(basis(state, [state['questions'][0]['id']])[1])
        sources = [item for item in items if item['kind'] == 'source']
        observations = [item for item in items if item['kind'] == 'observation']
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(observations), 2)
        question = next(item for item in items if item['kind'] == 'question')
        self.assertEqual(question['citation_refs'], [])
        self.assertTrue(question['related_refs'])
        self.assertTrue(all(item['question_ids'] == [state['questions'][0]['id']]
                            for item in sources + observations))
        packet = invoke(self.case, {'action': 'compose', 'section': 'key_evidence'})
        draft = {'entries': [
            {'node_id': 'key_evidence.one', 'cells': ['요청 기록', '요청 문자열 존재', '실행 성공 미입증'],
             'basis_keys': ['Q1', sources[0]['key']], 'status': 'saved'},
            {'node_id': 'key_evidence.two', 'cells': ['생성 기록', '프로세스 생성 기록 존재', '동일 작업 연결 미확인'],
             'basis_keys': [observations[1]['key']], 'status': 'saved'},
        ]}
        self.write('key_evidence', draft=draft)
        entries = status(current(self.case))['fields']['key_evidence']['entries']
        self.assertEqual(entries[0]['citation_refs'], sources[0]['citation_refs'])
        self.assertEqual(entries[1]['citation_refs'], observations[1]['citation_refs'])
        self.assertNotEqual(entries[0]['citation_refs'], entries[1]['citation_refs'])
        # Paged catalog reads recover every selectable record, without exposing
        # a different version or adopting the entire question's source set.
        seen = list(packet['items'])
        while packet.get('next_call'):
            packet = invoke(self.case, packet['next_call']['arguments'])
            seen.extend(packet['items'])
        self.assertEqual({i['key'] for i in seen}, {i['key'] for i in items})
        for item in seen:
            if item['kind'] in ('source', 'observation'):
                exact = invoke(self.case, item['evidence'][0]['read_call']['arguments'])
                expected = next(r for r in state['sources'] if r['id'] ==
                    (item.get('source_ref', {}).get('id') or item['id']))
                self.assertEqual(exact['source_ref']['version'], expected['version'])

    def test_all_fixed_regions_and_review_produce_authored_tables_and_exact_notes(self):
        self.finish()
        state = current(self.case)
        checked(state)
        self.assertEqual(len(state['report_narratives']), 1)
        self.assertTrue(status(current(Case(self.case.manifest)))['ready'])
        pages = project_body(state)
        self.assertEqual(len(pages), 3)
        self.assertIn('major_findings', json.dumps(pages))
        self.assertTrue(state['report_narratives'][0]['fields']['major_findings']['entries'][0]['citation_refs'])
        result = invoke(self.case, {'action': 'render'})
        self.assertTrue(result['files'])
        html = Path(next(path for path in result['files'] if path.endswith('.html'))).read_text()
        for text in ('공통 식별이 없고', '최초 시각 미확인', '보유 내용 미검토', '근거 주석'):
            self.assertIn(text, html)
        self.assertIn('[', json.dumps(compile_views(state)['integrated']['pages'][0]))
        self.assertFalse(status(state)['semantic_approval'])

    def test_unknown_explanations_complete_but_blank_or_missing_nodes_do_not(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'major_findings'})
        draft = self.draft('major_findings', packet, unavailable=True)
        for edit in ('node', 'blank', 'reason', 'keys'):
            changed = deepcopy(draft)
            if edit == 'node': changed['entries'].pop()
            if edit == 'blank': changed['entries'][0]['cells'][0] = '  '
            if edit == 'reason': changed['entries'][0]['unavailable_reason'] = ' '
            if edit == 'keys': changed['entries'][0].update(status='saved', basis_keys=[])
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.write('major_findings', draft=changed)
        self.finish(unavailable=True)
        self.assertTrue(status(current(self.case))['ready'])
        self.assertIn('선택된 두 기록의 범위', json.dumps(project_body(current(self.case)), ensure_ascii=False))

    def test_obsolete_three_sections_are_rejected_and_legacy_cannot_be_new_ready(self):
        for section in ('summary', 'findings', 'limits'):
            with self.assertRaisesRegex(ValueError, 'obsolete'):
                invoke(self.case, {'action': 'compose', 'section': section})
        state = current(self.case)
        from forsic_plugin.report_driven.narrative import basis
        ids = [q['id'] for q in state['questions']]
        state['report_narratives'] = [{'schema': 'forsic-report-narrative-1', 'question_ids': ids,
            'revision': 9, 'basis_id': basis(state, ids)[0], 'sections': {
                key: {'title': key, 'paragraphs': [{'text': 'legacy', 'basis_keys': ['Q1']}]}
                for key in ('summary', 'findings', 'limits')}}]
        self.assertFalse(status(state)['ready'])
        self.assertEqual(len(status(state)['missing_groups']), len(GROUPS))

    def test_idempotent_replay_stale_ref_and_changed_payload_conflict(self):
        first, request = self.write('major_findings')
        self.assertEqual(first, invoke(self.case, request))
        self.assertEqual(status(current(self.case))['revision'], 1)
        with self.assertRaisesRegex(ValueError, 'stale'):
            invoke(self.case, {**request, 'operation_id': 'stale-retry'})
        with self.assertRaisesRegex(ValueError, 'different mutation'):
            changed = deepcopy(request)
            changed['draft']['entries'][0]['cells'][0] += 'changed'
            invoke(self.case, changed)
        packet = invoke(self.case, {'action': 'compose', 'section': 'information_impact'})
        bad = self.draft('information_impact', packet)
        bad['entries'][0]['basis_keys'] = ['C9999']
        with self.assertRaisesRegex(ValueError, 'basis_keys'):
            self.write('information_impact', draft=bad)

    def test_forged_cross_case_and_cross_group_references_rejected(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'major_findings'})
        reference = packet['save_contract']['edit_ref']
        with self.assertRaisesRegex(ValueError, 'forged'):
            self.write('major_findings', edit_ref=reference[:-1] + ('A' if reference[-1] != 'A' else 'B'))
        with self.assertRaisesRegex(ValueError, 'reference'):
            self.write('information_impact', edit_ref=reference)
        with tempfile.TemporaryDirectory() as tmp:
            other, _, _ = fixture(tmp)
            # Same public case ID is deliberately insufficient for a valid edit.
            with self.assertRaisesRegex(ValueError, 'reference'):
                invoke(other, {'action': 'compose', 'section': 'major_findings', 'edit_ref': reference,
                    'operation_id': 'foreign-ledger', 'draft': self.draft('major_findings', packet)})
        from forsic_plugin.report_driven.narrative import _edit_reference
        progress = status(current(self.case))
        self.assertNotEqual(_edit_reference(self.case, progress, 'major_findings', {'_session_id': 'one'}),
                            _edit_reference(self.case, progress, 'major_findings', {'_session_id': 'two'}))

    def test_unreviewed_or_blocking_review_not_ready_and_prose_edit_invalidates_review(self):
        for group in WRITING_ORDER:
            self.write(group)
        self.assertFalse(status(current(self.case))['ready'])
        self.assertEqual(invoke(self.case, {'action': 'render'})['status'], 'document_review_required')
        with self.assertRaisesRegex(ValueError, 'every current template node'):
            self.review(omit_node=True)
        result, _ = self.review(blocking=True)
        self.assertFalse(result['ready'])
        self.assertEqual(result['review_status'], 'needs_revision')
        result, _ = self.review(unresolved=True)
        self.assertFalse(result['ready'])
        before_hash = current(self.case)['report_narratives'][0]['draft_hash']
        result, request = self.review()
        self.assertTrue(result['ready'])
        self.assertEqual(before_hash, current(self.case)['report_narratives'][0]['draft_hash'])
        self.assertEqual(result, invoke(self.case, request))
        self.write('scope')
        progress = status(current(self.case))
        self.assertFalse(progress['ready'])
        self.assertEqual(progress['review_status'], 'required')

    def test_correction_invalidates_dependency_fields_and_preserves_old_bundle_bytes(self):
        self.finish()
        rendered = invoke(self.case, {'action': 'render'})
        before = {path: Path(path).read_bytes() for path in rendered['files']}
        old = note(self.case, {'action': 'get', 'note_id': self.note['note_id']})['note']
        note(self.case, {'action': 'save', 'note_id': old['note_id'], 'revision': old['revision'],
            'question': old['question'], 'answer': '추가 기록에서도 공통 작업 연결은 미확인입니다.',
            'evidence_ids': old['evidence_ids'], 'correction_reason': '새 검토'})
        state = current(self.case)
        self.assertTrue(status(state)['stale'])
        self.assertTrue(status(state)['stale_fields'])
        self.assertIsNone(project_body(state))
        packet = invoke(self.case, {'action': 'render'})
        self.assertFalse(packet['rendered'])
        self.assertEqual(packet['status'], 'judgment_review_required')
        self.assertEqual(before, {path: Path(path).read_bytes() for path in before})
        self.assertTrue(bundles(self.case)[0]['stale'])
        with self.assertRaisesRegex(ValueError, 'judgment.*review'):
            invoke(self.case, {'action': 'compose', 'section': 'scope', 'operation_id': 'pending-judgment',
                'edit_ref': 'obsolete', 'draft': self.draft('scope', prepare(state, {}))})

    def test_authentication_key_rejects_symlink_and_nonregular_file(self):
        path = self.case.output / '.report-authoring-key'
        target = Path(self.tmp.name) / 'unrelated-key'
        target.write_bytes(b'x' * 32)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'regular local file'):
            invoke(self.case, {'action': 'compose'})
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(ValueError, 'regular local file'):
            invoke(self.case, {'action': 'compose'})
        self.assertEqual(target.read_bytes(), b'x' * 32)

    def test_dependency_matching_reuses_only_unchanged_material_and_invalidates_review(self):
        self.write('major_findings', keys=['Q1'])
        self.write('scope', keys=['C1'])
        state = current(self.case)
        state['questions'][0]['answer'] += ' newly qualified'
        progress = status(state)
        self.assertIn('major_findings', progress['stale_fields'])
        self.assertNotIn('major_findings', progress['fields'])
        self.assertIn('scope', progress['stale_fields'])
        self.assertNotIn('scope', progress['fields'])
        self.assertFalse(progress['ready'])
        self.assertEqual(progress['review_status'], 'required')

    def test_material_gaps_and_questions_cannot_silently_disappear(self):
        self.finish()
        state = current(self.case)
        state['gaps'][0]['impact'] = 'material'
        from forsic_plugin.report_driven.narrative import basis, material, _hash
        value = state['report_narratives'][0]
        value['basis_id'] = basis(state, value['question_ids'])[0]
        choices = {row['key']: row for row in material(basis(state, value['question_ids'])[1])}
        for field in value['fields'].values():
            for entry in field['entries']:
                entry['basis_keys'] = ['C1']
            field['dependencies'] = {'C1': _hash(choices['C1'])}
        progress = status(state)
        self.assertFalse(progress['ready'])
        self.assertTrue(progress['missing_material_gap_keys'])

    def test_resume_compaction_and_interruption_keep_saved_work_without_new_collection(self):
        self.write('major_findings')
        self.write('information_impact')
        before = current(self.case)
        reopened = Case(self.case.manifest)
        state = current(reopened)
        compacted = json.loads(json.dumps(state))
        packet = prepare(compacted, {}, reopened)
        self.assertEqual(packet['section'], 'key_evidence')
        self.assertEqual({field['section'] for field in packet['saved_fields']}, {'major_findings', 'information_impact'})
        self.assertEqual(before['sources'], state['sources'])
        self.assertEqual(before['questions'], state['questions'])
        self.assertEqual(before['meta']['content_revision'], state['meta']['content_revision'])
        self.assertTrue(packet['save_contract']['edit_ref'].startswith('re2.'))

    def test_unfinished_group_and_current_feedback_survive_reopen(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'major_findings'})
        partial = self.draft('major_findings', packet)
        partial.update(complete=False, entries=partial['entries'][:2])
        result, _ = self.write('major_findings', draft=partial)
        self.assertFalse(result['ready'])
        self.assertIn('major_findings', result['missing_groups'])
        from forsic_plugin.report_driven.narrative import progress_view
        state = current(Case(self.case.manifest))
        resumed = prepare(state, {}, self.case)
        self.assertEqual(resumed['section'], 'major_findings')
        self.assertEqual(len(resumed['saved_fields'][0]['entries']), 2)
        view = progress_view(state)
        self.assertEqual(next(group for group in view['groups'] if group['id'] == 'major_findings')['state'], 'in_progress')
        self.assertEqual(len(progress_view(None)['groups']), len(GROUPS))
        self.finish()
        self.write('major_findings')
        focused = invoke(self.case, {'action': 'compose', 'section': 'major_findings'})
        feedback = focused['current_feedback'][0]
        self.assertNotIn('reason', feedback)
        exact_feedback = invoke(self.case, feedback['read_call']['arguments'])
        self.assertEqual(next(item['value'] for item in exact_feedback['items'] if item['key'] == 'reason'),
                         '실제 사건 연결은 미확인으로 본문에 남겼습니다.')
        packet = invoke(self.case, {'action': 'compose', 'section': 'review'})
        self.assertIsNotNone(packet['previous_review'])
        self.assertFalse(status(current(self.case))['ready'])

    def test_read_packet_bound_explicit_partial_and_exact_source_contract_unchanged(self):
        state = current(self.case)
        packet = prepare(state, {}, self.case)
        evidence = [entry for item in packet['items'] for entry in item.get('evidence', [])]
        self.assertTrue(any('exact_excerpt' in entry for entry in evidence))
        state['questions'][0]['answer'] = '가' * 15000
        packet = prepare(state, {}, self.case)
        self.assertTrue(packet['items'][0]['body_omitted'])
        self.assertIn('read_call', packet['items'][0])
        self.assertNotIn('가' * 10000, json.dumps(packet))
        self.assertTrue(invoke(self.case, {'action': 'render', 'allow_partial': True})['files'])
        checked(current(self.case))

    def test_editorial_writes_do_not_change_current_judgments_or_evidence(self):
        before = current(self.case)
        self.finish()
        after = current(self.case)
        for field in ('sources', 'observations', 'questions', 'gaps', 'claims'):
            self.assertEqual(before[field], after[field])
        self.assertEqual(before['meta']['content_revision'], after['meta']['content_revision'])
        response = json.loads(self.case.invoke('forsic_reporting', {'action': 'compose'}))
        self.assertEqual(response['status'], 'editorial_ready')

    def test_reader_contract_reaches_native_tool_and_keeps_inference_with_unknowns(self):
        with patch('socket.socket', side_effect=AssertionError('No hidden writer/model call')):
            packet = json.loads(self.case.invoke('forsic_reporting', {'action': 'compose', 'section': 'major_findings'}))
            self.assertEqual(packet['field_contract']['cell_labels'], ['시점·요약', '판단'])
            self.assertIn('supported leading estimate', packet['writing'])
            self.assertIn('noun forms', packet['writing'])
            self.finish()
            state = current(self.case)
            before = deepcopy(state)
            pages = project_body(state)
        summary = next(b for b in pages[0]['blocks'] if b.get('region') == 'major_findings')
        self.assertEqual(summary['headers'], ['구분', '시점·요약', '판단', '근거'])
        self.assertEqual(summary['rows'][2][1:3], CONTENT['major_findings'][2])
        self.assertTrue(summary['cell_citation_refs'][2][-1])
        self.assertEqual(state, before)

    def test_report_identity_has_no_filename_or_report_date_fallback(self):
        packet = json.loads(self.case.invoke('forsic_reporting', {'action': 'compose', 'section': 'report_identity'}))
        self.assertEqual(packet['field_contract']['cell_labels'], ['Hostname', '대표 IP', '이미지 채증 시점'])
        self.finish()
        state = current(self.case)
        from forsic_plugin.report_driven.reader_projection import project_authored
        fields = status(state)['fields']
        state['scope']['evidence_labels'] = ['203.0.113.7_invented-host_2030-01-02.E01']
        state['meta']['generated_at'] = '2030-01-02T03:04:05Z'
        pages = project_authored(state, fields)
        meta = next(b for b in pages[0]['blocks'] if b.get('region') == 'metadata')
        self.assertEqual(meta['headers'], ['Hostname', '대표 IP', '이미지 채증 시점'])
        self.assertEqual(meta['rows'][0], CONTENT['report_identity'][0])
        self.assertNotIn('2030', json.dumps(meta, ensure_ascii=False))
        for label in ('사건관리번호', '시스템·조사기준', '작성 및 문서상태'):
            self.assertNotIn(label, json.dumps(meta, ensure_ascii=False))

    def test_old_writing_contract_cannot_relabel_cells_or_reuse_review(self):
        self.finish()
        state = current(self.case)
        state['report_narratives'][0]['template_version'] = 'forsic-integrated-v2'
        before = deepcopy(state)
        packet = prepare(state, {}, self.case)
        self.assertEqual(packet['status'], 'authoring_required')
        self.assertEqual(packet['section'], 'major_findings')
        self.assertEqual(set(packet['stale_fields']), {group[0] for group in GROUPS})
        self.assertEqual(set(packet['missing_groups']), {group[0] for group in GROUPS})
        self.assertIsNone(project_body(state))
        self.assertEqual(state, before)

    def test_unavailable_reason_keeps_compact_summary_and_hierarchical_judgment(self):
        self.finish(unavailable=True)
        table = next(b for b in project_body(current(self.case))[0]['blocks'] if b.get('region') == 'major_findings')
        self.assertEqual(table['rows'][0][1], '시점 미확인')
        self.assertIn('- 확인 한계:', table['rows'][0][2])

    def test_review_index_is_compact_complete_and_exact_large_fields_are_recoverable(self):
        self.finish()
        packet = invoke(self.case, {'action': 'compose', 'section': 'key_evidence'})
        draft = self.draft('key_evidence', packet, unavailable=True)
        draft['entries'][0]['cells'] = ['가' * 1900, '직접 관측 ' * 250, '미확인 연결 ' * 250]
        draft['entries'][0]['unavailable_reason'] = '중요한 한계 ' * 250
        self.write('key_evidence', draft=draft)
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        from forsic_plugin.report_driven.model_pages import MAX_BYTES, size
        self.assertLess(size(root), 18000)
        self.assertEqual(root['items'], [])
        self.assertFalse(any('entries' in field for field in root['saved_fields']))
        nodes = {node for field in root['saved_fields'] for node in field['node_ids']}
        self.assertEqual(nodes, set(root['reviewed_node_ids']))
        self.assertEqual(len(root['saved_fields']), len(GROUPS))

        def exact(call):
            result = invoke(self.case, call['arguments'])
            self.assertLessEqual(size(result), MAX_BYTES)
            if 'text' in result:
                chunks = [result['text']]
                while result['next_call']:
                    result = invoke(self.case, result['next_call']['arguments'])
                    self.assertLessEqual(size(result), MAX_BYTES)
                    chunks.append(result['text'])
                return ''.join(chunks)
            if 'value' in result:
                return result['value']
            items = list(result['items'])
            while result['next_call']:
                result = invoke(self.case, result['next_call']['arguments'])
                self.assertLessEqual(size(result), MAX_BYTES)
                items.extend(result['items'])
            values = [item['value'] if 'value' in item else exact(item['read_call']) for item in items]
            return dict(zip([item['key'] for item in items], values)) if items and 'key' in items[0] else values

        indexed = exact(root['review_index_call'])
        self.assertEqual({node for field in indexed for node in field['node_ids']}, nodes)
        field = next(field for field in root['saved_fields'] if field['section'] == 'key_evidence')
        recovered = exact(field['read_call'])
        self.assertEqual(recovered['entries'][0]['cells'], draft['entries'][0]['cells'])
        self.assertEqual(recovered['entries'][0]['unavailable_reason'], draft['entries'][0]['unavailable_reason'])
        self.assertTrue(recovered['entries'][0]['citation_refs'])

    def test_draft_pages_reject_changed_prose_or_basis_and_preserve_exact_source_links(self):
        self.finish()
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        material = invoke(self.case, {**root['material_read_call']['arguments'], 'limit': 1})
        first = material['items'][0]
        if 'value' in first:
            links = first['value']['evidence']
        else:
            evidence = invoke(self.case, {**first['read_call']['arguments'], 'pointer': first['pointer'] + '/evidence'})
            links = [item['value'] for item in evidence['items']]
        self.assertTrue(links)
        for link in links:
            source = invoke(self.case, link['read_call']['arguments'])
            self.assertEqual(source['source_ref']['id'], link['read_call']['arguments']['source_id'])
            self.assertEqual(source['source_ref']['version'], link['read_call']['arguments']['source_version'])
        old = root['saved_fields'][0]['read_call']['arguments']
        group = root['saved_fields'][0]['section']
        packet = invoke(self.case, {'action': 'compose', 'section': group})
        changed = self.draft(group, packet)
        changed['entries'][0]['cells'][0] += ' 선택 범위의 한계.'
        self.write(group, draft=changed)
        with self.assertRaisesRegex(ValueError, 'draft or basis changed'):
            invoke(self.case, old)
        state = current(self.case)
        fresh = prepare(state, {'section': 'review'}, self.case)
        state['questions'][0]['answer'] += ' changed basis'
        with self.assertRaisesRegex(ValueError, 'draft or basis changed'):
            prepare(state, fresh['saved_fields'][0]['read_call']['arguments'], self.case)

    def test_review_save_keeps_immutable_draft_reads_but_revisions_review_reads(self):
        self.finish()
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        draft_call = root['saved_fields'][0]['read_call']['arguments']
        review_call = root['previous_review']['read_call']['arguments']
        before = invoke(self.case, draft_call)
        result, _ = self.review()
        after = invoke(self.case, draft_call)
        self.assertEqual(before, after)
        self.assertEqual(root['snapshot_id'], result['snapshot_id'])
        with self.assertRaisesRegex(ValueError, 'review changed'):
            invoke(self.case, review_call)

    def test_native_bad_draft_reads_return_scoped_recovery_without_changing_prose(self):
        self.finish()
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        before = status(current(self.case))['draft_hash']
        for change in ({'pointer': '/unknown'}, {'pointer': '/fields/no-such-section'},
                       {'pointer': '/index', 'snapshot_id': 'old-snapshot'}):
            with self.subTest(change=change):
                request = {**root['review_index_call']['arguments'], **change}
                result = json.loads(self.case.invoke('forsic_reporting', request))
                self.assertEqual(result['error_code'], 'report_authoring_reference_invalid')
                self.assertFalse(result['saved'])
                recovery = result['recovery']['arguments']
                self.assertEqual(recovery['question_ids'], root['question_ids'])
                self.assertNotIn('draft', recovery)
                self.assertNotIn('pointer', recovery)
                refreshed = json.loads(self.case.invoke('forsic_reporting', recovery))
                page = json.loads(self.case.invoke('forsic_reporting', refreshed['review_index_call']['arguments']))
                self.assertNotIn('error', page)
                self.assertEqual(status(current(self.case))['draft_hash'], before)

    def test_stale_or_incomplete_write_offers_read_recovery_never_replays_prose(self):
        original = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        self.write('major_findings')
        request = {'action': 'compose', 'section': 'scope',
                   'question_ids': original['question_ids'], 'operation_id': 'old-scope-edit',
                   'edit_ref': original['save_contract']['edit_ref'], 'draft': self.draft('scope', original)}
        before = status(current(self.case))['draft_hash']
        for changes in ({}, {'section': None}):
            result = json.loads(self.case.invoke('forsic_reporting', {**request, **changes}))
            self.assertFalse(result['saved'])
            recovery = result['recovery']['arguments']
            self.assertNotIn('draft', recovery)
            self.assertNotIn('edit_ref', recovery)
            self.assertEqual(recovery['question_ids'], original['question_ids'])
            self.assertNotIn('error', json.loads(self.case.invoke('forsic_reporting', recovery)))
            self.assertEqual(status(current(self.case))['draft_hash'], before)

    def test_save_supplies_exact_new_read_calls_and_rejects_previous_snapshot(self):
        self.write('major_findings')
        original = invoke(self.case, {'action': 'compose', 'section': 'major_findings'})
        prior = original['saved_fields'][0]['read_call']['arguments']
        draft = self.draft('major_findings', original)
        draft['entries'][0]['cells'][1] += ' 추가적인 표현 정리.'
        saved, _ = self.write('major_findings', draft=draft)
        page = invoke(self.case, saved['saved_field_read_call']['arguments'])
        self.assertEqual(page['snapshot_id'], saved['snapshot_id'])
        self.assertNotIn('error', invoke(self.case, saved['review_index_call']['arguments']))
        with self.assertRaisesRegex(ValueError, 'draft or basis changed'):
            invoke(self.case, prior)

    def test_save_returns_fresh_cas_refs_without_repeating_whole_document(self):
        original = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        result, _ = self.write('major_findings')
        request = {'action': 'compose', 'section': 'scope', 'operation_id': 'fresh-scope',
                   'edit_ref': result['edit_refs']['scope'], 'draft': self.draft('scope', original)}
        stale = {**request, 'edit_ref': original['save_contract']['edit_ref']}
        with self.assertRaisesRegex(ValueError, 'stale'):
            invoke(self.case, stale)
        saved = invoke(self.case, request)
        self.assertTrue(saved['saved'])
        self.assertNotIn('fields', saved)
        self.assertNotEqual(result['edit_refs']['scope'], saved['edit_refs']['scope'])

    def test_review_requires_explicit_comparison_but_unknowns_need_no_invented_basis(self):
        self.finish()
        legacy = current(self.case)
        legacy['report_narratives'][0]['review'].pop('comparisons')
        self.assertEqual(status(legacy)['review_status'], 'required')
        self.assertFalse(status(legacy)['ready'])
        _, request = self.review()
        for kind in ('missing', 'blank', 'foreign_node', 'foreign_basis'):
            changed = deepcopy(request['review'])
            if kind == 'missing': changed.pop('comparisons')
            if kind == 'blank': changed['comparisons'][0]['limits'] = ' '
            if kind == 'foreign_node': changed['comparisons'][0]['node_ids'] = ['invented']
            if kind == 'foreign_basis': changed['comparisons'][0]['basis_keys'] = ['C9999']
            packet = invoke(self.case, {'action': 'compose', 'section': 'review'})
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                invoke(self.case, {**request, 'operation_id': 'bad-comparison-' + kind,
                                  'edit_ref': packet['save_contract']['edit_ref'], 'review': changed})
        packet = invoke(self.case, {'action': 'compose', 'section': 'review'})
        unknown = deepcopy(request['review'])
        unknown['comparisons'][0].update(basis_keys=[], source_support='해당 승인 자료를 확보하지 못했습니다.',
                                         limits='승인 여부 미확인', assessment='미확인으로 유지합니다.')
        result = invoke(self.case, {**request, 'operation_id': 'honest-unknown-comparison',
                                   'edit_ref': packet['save_contract']['edit_ref'], 'review': unknown})
        self.assertTrue(result['ready'])
        self.assertFalse(result['semantic_approval'])
        self.write('scope', keys=['Q1'])
        packet = invoke(self.case, {'action': 'compose', 'section': 'review'})
        comparison = deepcopy(unknown)
        comparison['comparisons'][0].update(node_ids=['scope'], basis_keys=['C1'])
        result = invoke(self.case, {**request, 'operation_id': 'uncited-scope-counterevidence',
                                   'edit_ref': packet['save_contract']['edit_ref'], 'review': comparison})
        self.assertTrue(result['ready'])


if __name__ == '__main__':
    unittest.main()
