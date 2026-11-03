"""Synthetic read-only case/goal projection and completion-boundary tests."""
from contextlib import ExitStack, closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hermes_cli.goals import GoalState, GoalContract, goal_identity
from forsic_plugin import investigation_context as context
from forsic_plugin.report_driven.host import current, invoke, find
from forsic_plugin.report_driven.investigation_state import validate_execution_context
from test_report_driven import fixture


class InvestigationContextTests(unittest.TestCase):
    def test_no_compose_receipts_does_not_replay_report_state(self):
        from forsic_plugin.report_continuation import projection
        resolved = context.resolve_context(**self.kwargs())
        with patch('forsic_plugin.report_driven.narrative.status',
                   side_effect=AssertionError('No report view was delivered')):
            self.assertIsNone(projection(resolved, {}, ['Q-' + self.note['note_id']]))

    def test_unsaved_public_update_survives_compression_and_later_save(self):
        import time
        self.case.record('post_api_request', dict(api_request_id='public-checkpoint',
            assistant_content_chars=50), self.sid)
        self.case.record('tool_start', dict(tool='forsic_read',
            native_call={'api_request_id': 'public-checkpoint'}), self.sid)
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('checkpoint-child', parent=self.sid)
        before = current(self.case)
        result = context.post_context_compaction(**self.kwargs('checkpoint-child'))
        body = json.loads(result['context'])
        self.assertEqual(body['answer_updates']['checkpoint']['status'], 'pending')
        self.assertEqual(body['answer_updates']['questions'][0]['expected_revision'], self.note['revision'])
        self.assertIn(self.note['answer'], result['context'])
        self.assertEqual(current(self.case), before)
        # A later analyst commit supersedes the pending navigation, while the
        # original public/source history and native ownership remain intact.
        self.note['updated_at'] = time.time()
        self.note['judgment_updated_at'] = self.note['updated_at']
        self.note['answer'] = 'Corrected interpretation with an explicit remaining check'
        self.save_note(next_checks=['Read the retained completion receipt'])
        saved = current(self.case)
        restored = context.post_context_compaction(**self.kwargs('checkpoint-child'))
        self.assertNotIn('checkpoint', json.loads(restored['context'])['answer_updates'])
        self.assertIn(self.note['answer'], restored['context'])
        self.assertEqual(current(self.case), saved)

    def test_selected_delivery_phase_outranks_old_compose_after_compression(self):
        state, packet, _ = self.report_receipt()
        resolved = context.resolve_context(**self.kwargs())
        task = {'kind': 'report_followup_task', 'question_ids': [q['id'] for q in state['questions']],
                'source_goal_id': 'previous', 'legacy_tracker_ids': []}
        with patch('forsic_plugin.report_followup_work.classify', return_value=task):
            with patch('forsic_plugin.report_followup_work.selected_version', return_value={
                    'stage': 'deliver_selected_version', 'pending': None, 'bundle_id': 'current-bundle',
                    'selection_receipt': 'current-selection'}):
                body = json.loads(context.project_context(resolved, state)[0])
                self.assertEqual(body['reports']['continuation']['stage'], 'deliver_selected_version')
                self.assertEqual(body['reports']['continuation']['bundle_id'], 'current-bundle')
                self.assertIsNone(body['reports']['continuation']['next_call'])
            # The trusted gate rejects a selection for a different current draft.
            with patch('forsic_plugin.report_followup_work.selected_version', return_value={
                    'stage': 'author_current_version', 'pending': 'Current draft needs authoring'}):
                body = json.loads(context.project_context(resolved, state)[0])
                self.assertEqual(body['reports']['continuation']['snapshot_id'], packet['snapshot_id'])
                self.assertEqual(body['reports']['continuation']['next_call'], packet['next_call'])

    def test_report_core_navigation_survives_node_history_budget_pressure(self):
        state, packet, _ = self.report_receipt()
        resolved = context.resolve_context(**self.kwargs())
        reports = context._report_context(resolved, state, state['questions'])
        reports['continuation']['delivered_content']['delivered_node_ids'] = ['synthetic-' + 'x'*150] * 12
        with patch.object(context, '_report_context', return_value=reports):
            text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        self.assertLessEqual(len(text.encode()), 5000)
        core = body['reports']['continuation']
        self.assertEqual(core['snapshot_id'], packet['snapshot_id'])
        self.assertEqual(core['next_call'], packet['next_call'])
        self.assertNotIn('delivered_node_ids', core.get('delivered_content', {}))
        self.assertEqual(body['active_questions'][0]['id'], state['questions'][0]['id'])

    def authored_state(self):
        from forsic_plugin.report_driven.narrative import status, TEMPLATE_VERSION
        state = current(self.case)
        progress = status(state)
        fields = {'major_findings': {'complete': False, 'entries': [
            dict(node_id='major_findings.synthetic' + str(i), cells=['Synthetic text', 'Scoped detail'],
                 basis_keys=['Q1'], citation_refs=[], status='saved') for i in range(2)]}}
        state['report_narratives'] = [dict(schema='forsic-report-narrative-2', template_version=TEMPLATE_VERSION,
            question_ids=progress['question_ids'], basis_id=progress['basis_id'], revision=4,
            draft_revision=3, fields=fields, review=None)]
        return state

    def authored_read(self, state, pointer, *, packet_change=None, session=None):
        from forsic_plugin.report_driven.narrative import prepare, status, _review_identity
        args = dict(action='compose', question_ids=status(state)['question_ids'], section='review',
                    pointer=pointer, snapshot_id=_review_identity(status(state)))
        packet = prepare(state, args, self.case)
        if packet_change:
            packet.update(packet_change)
        sid = session or self.sid
        started = self.case.record('tool_start', dict(tool='forsic_reporting', arguments=args), sid)
        self.case.record('tool_result', dict(packet, tool='forsic_reporting', started_id=started), sid)
        return packet

    def test_authored_delivery_survives_second_compression_and_navigates_unread_node(self):
        from forsic_plugin.report_continuation import projection
        state = self.authored_state()
        self.authored_read(state, '/fields/major_findings/entries/0')
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('report-first-child', parent=self.sid, end='compression')
        self.session('report-second-child', parent='report-first-child')
        resolved = context.resolve_context(**self.kwargs('report-second-child'))
        view = projection(resolved, state, [q['id'] for q in state['questions']])
        self.assertEqual(view['delivered_content']['delivered_node_ids'], ['major_findings.synthetic0'])
        self.assertEqual(view['delivered_content']['role'], 'delivered_not_semantically_reviewed')
        self.assertEqual(view['next_call']['arguments']['pointer'], '/fields/major_findings/entries/1')
        # Legacy collection pages carry complete nodes in items[].value.
        self.authored_read(state, '/fields/major_findings/entries', session='report-second-child')
        view = projection(resolved, state, [q['id'] for q in state['questions']])
        self.assertEqual(view['delivered_content']['delivered_node_count'], 2)
        self.assertIsNone(view['next_call'])
        self.assertFalse(view['delivered_content']['window_partial'])

    def test_authored_delivery_excludes_partial_nodes_and_stale_snapshot(self):
        from forsic_plugin.report_continuation import projection
        state = self.authored_state()
        self.authored_read(state, '/fields/major_findings/entries/0', packet_change={'complete_value': False})
        self.authored_read(state, '/fields/major_findings/entries/1', packet_change={'snapshot_id': 'rd2.obsolete'})
        self.authored_read(state, '/index')
        resolved = context.resolve_context(**self.kwargs())
        view = projection(resolved, state, [q['id'] for q in state['questions']])
        self.assertEqual(view['delivered_content']['delivered_node_count'], 0)
        self.assertEqual(view['next_call']['arguments']['pointer'], '/fields/major_findings/entries/0')

    def test_authored_delivery_recognizes_old_complete_node_member_page(self):
        from forsic_plugin.report_continuation import projection
        state = self.authored_state()
        entry = state['report_narratives'][0]['fields']['major_findings']['entries'][0]
        self.authored_read(state, '/fields/major_findings/entries/0', packet_change={
            'value': None, 'complete_value': True,
            'items': [{'key': key, 'value': value} for key, value in entry.items()]})
        resolved = context.resolve_context(**self.kwargs())
        view = projection(resolved, state, [q['id'] for q in state['questions']])
        self.assertEqual(view['delivered_content']['delivered_node_ids'], [entry['node_id']])
        self.assertEqual(view['next_call']['arguments']['pointer'], '/fields/major_findings/entries/1')

    def test_saved_complete_review_keeps_render_stage_without_restarting_unread_nodes(self):
        from forsic_plugin.report_continuation import projection
        from forsic_plugin.report_driven.narrative import status, material, basis, _draft_hash, _review_identity
        from forsic_plugin.report_driven.template_contract import WRITING_ORDER, REVIEW_CHECKS
        from forsic_plugin.report_driven.authoring_policy import RESOLUTION_NODES
        state = self.authored_state()
        narrative = state['report_narratives'][0]
        keys = [row['key'] for row in material(basis(state, narrative['question_ids'])[1])]
        narrative['fields'] = {group: {'complete': True, 'entries': [dict(
            node_id=group + '.synthetic', cells=['Scoped synthetic text'], basis_keys=keys,
            citation_refs=[], status='saved')]} for group in WRITING_ORDER}
        narrative['review'] = dict(basis_id=narrative['basis_id'], draft_hash=_draft_hash(narrative['fields']),
            draft_revision=narrative['draft_revision'], comparisons=[{'statement': 'Synthetic review'}],
            checks=[{'check': key, 'result': 'satisfied'} for key in REVIEW_CHECKS], findings=[],
            answerability_checks=[{'node_id': node, 'outcome': 'limited'} for node in RESOLUTION_NODES])
        progress = status(state)
        self.assertTrue(progress['ready'])
        call = {'tool': 'forsic_reporting', 'arguments': {'action': 'render', 'question_ids': progress['question_ids']}}
        started = self.case.record('tool_start', dict(tool='forsic_reporting', arguments={
            'action': 'compose', 'question_ids': progress['question_ids'], 'expected_revision': 3,
            'review': {'synthetic': True}}), self.sid)
        self.case.record('tool_result', dict(tool='forsic_reporting', started_id=started, saved=True,
            snapshot_id=_review_identity(progress), question_ids=progress['question_ids'], next_call=call), self.sid)
        resolved = context.resolve_context(**self.kwargs())
        view = projection(resolved, state, progress['question_ids'])
        self.assertEqual(view['stage'], 'render_required')
        self.assertEqual(view['review_status'], 'complete')
        self.assertEqual(view['delivered_content']['delivered_node_count'], 0)
        self.assertEqual(view['next_call'], call)

    def report_receipt(self, *, session=None, snapshot=None, error=False, when=None, next_call=None):
        from forsic_plugin.report_driven.narrative import prepare, status, _review_identity
        state = current(self.case)
        ids = [q['id'] for q in state['questions']]
        sid = session or self.sid
        args = dict(action='compose', question_ids=ids, section='review',
                    pointer='/material', snapshot_id=_review_identity(status(state, ids)), limit=1)
        packet = prepare(state, args, self.case)
        self.assertIsNotNone(packet['next_call'])
        if snapshot:
            packet['snapshot_id'] = snapshot
        if next_call:
            packet['next_call'] = next_call
        if error:
            packet['error'] = 'synthetic failed delivery'
        started = self.case.record('tool_start', dict(tool='forsic_reporting', arguments=args), sid)
        if when is not None:
            with self.case.connect() as db:
                db.execute('UPDATE events SET time=? WHERE id=?', (when, started))
        result = self.case.record('tool_result', dict(packet, tool='forsic_reporting', started_id=started), sid)
        return state, packet, result

    def test_report_page_continuation_survives_compression_read_only(self):
        state, packet, result = self.report_receipt()
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('report-compressed-child', parent=self.sid)
        resolved = context.resolve_context(**self.kwargs('report-compressed-child'))
        before = self.case.db.read_bytes()
        text, _, _ = context.project_context(resolved, state)
        restored = json.loads(text)['reports']['continuation']
        self.assertEqual(restored['receipt_id'], result)
        self.assertEqual(restored['snapshot_id'], packet['snapshot_id'])
        self.assertEqual(restored['next_call'], packet['next_call'])
        self.assertEqual(restored['last_read']['pointer'], '/material')
        self.assertLessEqual(len(text.encode()), 5000)
        self.assertNotIn('edit_ref', json.dumps(restored))
        compact, _, _ = context.project_context(resolved, state, max_bytes=3500)
        compact_body = json.loads(compact)
        self.assertLessEqual(len(compact.encode()), 3500)
        self.assertTrue(compact_body['reports']['continuation_omitted'])
        self.assertEqual(compact_body['active_questions'][0]['id'], state['questions'][0]['id'])
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(current(self.case), state)

    def test_report_continuation_ignores_other_session_old_goal_and_failed_receipts(self):
        state, packet, result = self.report_receipt()
        self.report_receipt(session='foreign')
        self.report_receipt(when=self.goal.created_at - 1)
        self.report_receipt(error=True)
        resolved = context.resolve_context(**self.kwargs())
        restored = context._report_context(resolved, state, state['questions'])['continuation']
        self.assertEqual(restored['receipt_id'], result)
        self.assertEqual(restored['next_call'], packet['next_call'])

    def test_report_continuation_does_not_restore_stale_snapshot_or_write_contract(self):
        state, _, _ = self.report_receipt()
        self.report_receipt(snapshot='rd2.obsolete')
        resolved = context.resolve_context(**self.kwargs())
        self.assertNotIn('continuation', context._report_context(resolved, state, state['questions']))
        self.report_receipt(next_call={'tool': 'forsic_reporting', 'arguments': {
            'action': 'compose', 'question_ids': [state['questions'][0]['id']], 'draft': {'unsafe': True}}})
        restored = context._report_context(resolved, state, state['questions'])['continuation']
        self.assertIsNone(restored['next_call'])

    def test_report_continuation_uses_latest_save_navigation_and_current_review_stage(self):
        state, packet, _ = self.report_receipt()
        ids = [q['id'] for q in state['questions']]
        next_call = {'tool': 'forsic_reporting', 'arguments': {'action': 'compose', 'question_ids': ids}}
        started = self.case.record('tool_start', dict(tool='forsic_reporting', arguments={
            'action': 'compose', 'question_ids': ids, 'expected_revision': -1,
            'review': {'synthetic': True}}), self.sid)
        saved = self.case.record('tool_result', dict(tool='forsic_reporting', started_id=started,
            saved=True, snapshot_id=packet['snapshot_id'], question_ids=ids,
            section='review', next_call=next_call, edit_refs={'review': 'must-not-restore'}), self.sid)
        resolved = context.resolve_context(**self.kwargs())
        restored = context._report_context(resolved, state, state['questions'])['continuation']
        self.assertEqual(restored['receipt_id'], saved)
        self.assertEqual(restored['next_call'], next_call)
        from forsic_plugin.report_driven.narrative import status
        self.assertEqual(restored['review_status'], status(state, ids)['review_status'])
        self.assertNotIn('must-not-restore', json.dumps(restored))
        self.save_note(answer='Synthetic changed judgment')
        changed = current(self.case)
        self.assertNotIn('continuation', context._report_context(resolved, changed, changed['questions']))

    def test_derived_history_does_not_displace_second_current_question(self):
        from test_derived_observation_scope import triage_receipt
        from forsic_plugin.report_driven.host import checked
        self.note.update(answer='Observation remains uncertain.', reason='Compare retained record.',
                         gaps=[], critical_gaps=[], alternatives=[], next_checks=[])
        self.save_note(priority='contextual')
        invoke(self.case, dict(action='question', payload=dict(
            operation_id='second-current-question', question='Was a scheduled task executed?',
            scope='Selected synthetic configuration', answer='Execution unknown. '+'x'*50,
            priority='contextual'),
            _goal_id=goal_identity(self.goal), _session_id=self.sid, _question_contract='v2'))
        triage_receipt(self.case, self.sid, state='partial')
        state = current(self.case)
        checked(state)
        before = deepcopy(state)
        resolved = context.resolve_context(**self.kwargs())
        ids = [q['id'] for q in state['questions']]
        # Both questions have the same priority; random UUID ordering used to
        # decide whether the larger first record displaced the second answer.
        for order in (ids, list(reversed(ids))):
            def ordered(values, *, key):
                return sorted(values, key=lambda q: order.index(q['id'])) if values and 'priority' in values[0] else sorted(values, key=key)
            with self.subTest(order=order), patch.object(context, 'sorted', side_effect=ordered, create=True):
                text, _, _ = context.project_context(resolved, state)
                body = json.loads(text)
                self.assertLessEqual(len(text.encode()), 5000)
                self.assertEqual({q['id'] for q in body['active_questions']}, set(ids))
                self.assertEqual({q['id']: q['answer_is_model_assessment'] for q in body['active_questions']},
                                 {q['id']: q['answer'] for q in state['questions']})
                self.assertTrue(all(q['details_call']['arguments']['question_id'] == q['id']
                                    for q in body['active_questions']))
                self.assertEqual(body['omitted']['active_questions'], 0)
                self.assertGreater(body['omitted']['derived_observations'], 0)
        self.assertEqual(state, before)

    def test_derived_input_navigation_survives_compaction_without_displacing_current_answer(self):
        from test_derived_observation_scope import triage_receipt
        for state in ('complete', 'partial', 'skipped'):
            triage_receipt(self.case, self.sid, state=state)
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('derived-compressed-child', parent=self.sid)
        resolved = context.resolve_context(**self.kwargs('derived-compressed-child'))
        state = current(self.case)
        before = deepcopy(state)
        text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        self.assertLessEqual(len(text.encode()), 5000)
        self.assertEqual(body['active_questions'][0]['answer_is_model_assessment'], self.note['answer'])
        derived = body['derived_observations'][0]
        self.assertEqual(derived['kind'], 'triage')
        self.assertIn(derived['coverage']['state'], ('skipped', 'partial'))
        selected = derived['selected_inputs'][0]
        self.assertIn(selected['state'], ('skipped', 'partial'))
        ref = selected['source_ref']
        self.assertEqual(ref['version'], next(source['version'] for source in state['sources'] if source['id'] == ref['id']))
        self.assertEqual(derived['read_call']['arguments']['pointer'], '/coverage')
        self.assertEqual(body['scope_inventory']['counts']['body_read'], 0)
        self.assertEqual(body['derived_scope_inventory']['omitted']['receipts'] + len(
            body['derived_observations']) + body['omitted']['derived_observations'], 3)
        text, _, _ = context.project_context(resolved, state, max_bytes=3500)
        small = json.loads(text)
        self.assertLessEqual(len(text.encode()), 3500)
        self.assertEqual(small['active_questions'][0]['answer_is_model_assessment'], self.note['answer'])
        self.assertGreater(small['omitted']['derived_observations'], 0)
        self.assertEqual(state, before)

    def test_compaction_projects_actual_query_scope_for_this_conversation_without_body_or_verdict(self):
        partial=self.case.record('tool_result',dict(tool='forsic_search',path='synthetic.log',search_text='literal',
            match_type='casefold_literal',matches=[],bytes_scanned=80,coverage_complete=False,scan_exhausted=False,
            next_cursor={'state':'retained'},stop_reason='match_limit',scope_note='Not whole scope'),self.sid)
        other=self.case.record('tool_result',dict(tool='forsic_search',path='other.log',search_text='foreign',
            coverage_complete=False,scan_exhausted=False,next_cursor={'state':'other'}),'other-conversation')
        resolved=context.resolve_context(self.sid,intake_root=self.intake,native_db=self.native)
        before=current(self.case);text,_,_=context.project_context(resolved,before);view=json.loads(text)
        facts={f['source_id']:f for f in view['observed_scope']}
        self.assertIn(partial,facts);self.assertNotIn(other,facts)
        self.assertFalse(facts[partial]['observed']['coverage_complete'])
        self.assertEqual(facts[partial]['observed']['search_text'],'literal')
        self.assertTrue(facts[partial]['observed']['has_next_cursor'])
        self.assertNotIn('citation_ref',json.dumps(facts));self.assertEqual(current(self.case),before)

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.intake = self.root / 'intake'
        self.home = self.root / 'native'; self.home.mkdir()
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.native = self.home / 'state.db'
        self.case, self.sources, self.note = fixture(self.intake / 'cases' / 'synthetic')
        self.sid = 'synthetic-native-root'
        self.goal = GoalState(goal='Answer the synthetic scope', created_at=1.0,
                              contract=GoalContract(verification='Explain the supplied records'))
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT,model_config TEXT)')
            db.execute('CREATE TABLE state_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.session(self.sid, goal=self.goal)
        self.bind(self.sid, self.case)
        self.save_note(priority='decision_critical')

    def bind(self, sid, case):
        path = self.intake / (hashlib.sha256(sid.encode()).hexdigest() + '.json')
        path.write_text(json.dumps({'manifest': str(case.manifest)}))

    def session(self, sid, *, parent=None, goal=None, end=None, config=None):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)',
                       (sid, parent, end, 'tui', json.dumps(config or {})))
            if goal:
                db.execute('INSERT OR REPLACE INTO state_meta VALUES (?,?)', ('goal:' + sid, goal.to_json()))

    def save_note(self, **fields):
        self.note = deepcopy(self.note)
        self.note['revision'] += 1
        self.note.update(goal_id=goal_identity(self.goal), session_id=self.sid)
        self.note.setdefault('investigation', {}).update(fields)
        self.case.record('note', self.note, self.sid)

    def kwargs(self, sid=None):
        return {'session_id': sid or self.sid, 'intake_root': self.intake, 'native_db': self.native}

    def prepare(self):
        return context.goal_evaluation_context(goal_id=goal_identity(self.goal), **self.kwargs())

    def validate(self, prepared):
        return context.goal_evaluation_context(goal_id=goal_identity(self.goal), phase='validate',
            expected_revision={'forsic': prepared['revision']}, **self.kwargs())

    def test_prepare_is_goal_bound_and_marks_model_state_not_evidence(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'incomplete')
        body = json.loads(result['context'])
        self.assertIn('NOT original evidence', body['notice'])
        self.assertEqual(body['goal_id'], goal_identity(self.goal))
        self.assertEqual(body['active_questions'][0]['priority'], 'decision_critical')
        self.assertTrue(body['active_questions'][0]['alternatives'])
        self.assertTrue(body['active_questions'][0]['next_checks'])

    def test_mixed_work_states_survive_clipping_and_do_not_create_a_verdict(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        q = deepcopy(state['questions'][0]); state['questions'] = []
        for i in range(40):
            value = deepcopy(q)
            value.update(id='Q-mixed-' + str(i), question='Large question ' * 200,
                         work_state='blocked_external' if i < 39 else 'active')
            state['questions'].append(value)
        text, _, _ = context.project_context(resolved, state)
        body = json.loads(text); inventory = body['work_inventory']
        self.assertEqual(inventory['important_question_states']['blocked_external'], 39)
        self.assertEqual(inventory['important_question_states']['active'], 1)
        self.assertGreater(body['omitted']['active_questions'], 0)
        self.assertNotIn('verdict', inventory)
        self.assertLessEqual(len(text.encode('utf-8')), 5000)
        self.assertTrue(body['completion']['pending_count'])

    def test_work_inventory_excludes_other_goal_and_reports_unknown_priority(self):
        self.save_note(priority='unassessed', work_state='open')
        resolved = context.resolve_context(**self.kwargs()); state = current(self.case)
        other = deepcopy(state['questions'][0]); other.update(id='Q-other', goal_id='other-goal', work_state='blocked_external')
        state['questions'].append(other)
        text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        self.assertEqual(body['work_inventory']['priority_unassessed'], 1)
        self.assertEqual(body['work_inventory']['question_states']['blocked_external'], 0)
        self.assertEqual(body['omitted']['other_goal_or_unbound_questions'], 1)
        self.assertEqual(body['active_questions'][0]['revision'], self.note['revision'])

    def test_compaction_keeps_owned_writer_handles_and_material_review_navigation(self):
        from forsic_plugin.independent_review import review_state
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        original = deepcopy(state)
        review = review_state(resolved, state)
        review.update(status='received', pending_finding_ids=['finding-one', 'finding-two'], requires_followup=True)
        with patch('forsic_plugin.independent_review.review_state', return_value=review):
            text, _, _ = context.project_context(resolved, state, max_bytes=3500)
        body = json.loads(text)
        navigation = body['answer_updates']
        self.assertEqual(navigation['tool'], 'forsic_question')
        self.assertEqual(navigation['questions'], [{'question_id': state['questions'][0]['id'],
            'expected_revision': self.note['revision'],
            'adopted_claim_count': len(state['questions'][0]['claim_refs']), 'judgment_review_required': False}])
        self.assertEqual(navigation['review_response_field'], 'review_responses')
        self.assertEqual(body['independent_review']['pending_count'], 2)
        self.assertEqual(body['independent_review']['pending_scope'], 'material')
        self.assertEqual(body['independent_review']['lookup'], {'tool': 'forsic_reporting',
            'arguments': {'action': 'review_inbox'}})
        self.assertLessEqual(len(text.encode()), 3500)
        self.assertEqual(state, original)

    def test_question_navigation_does_not_borrow_ownership_or_treat_unknown_review_as_empty(self):
        from forsic_plugin.independent_review import review_state
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        template = deepcopy(state['questions'][0])
        for index in range(3):
            state['questions'].append({**deepcopy(template), 'id': 'Q-owned-'+str(index)})
        state['questions'].append({**deepcopy(template), 'id': 'Q-foreign', 'session_id': 'foreign'})
        review = review_state(resolved, state)
        review.update(status='unavailable', pending_finding_ids=[])
        with patch('forsic_plugin.independent_review.review_state', return_value=review):
            text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        navigation = body['answer_updates']
        self.assertEqual(len(navigation['questions']), 2)
        self.assertEqual(navigation['omitted_questions'], 2)
        self.assertNotIn('Q-foreign', json.dumps(navigation))
        self.assertIsNone(body['independent_review']['pending_count'])
        self.assertEqual(body['independent_review']['status'], 'unavailable')

    def test_compaction_keeps_current_judgment_lookup_before_observation_detail(self):
        for work_state, section in (('active', 'active_questions'), ('scoped_closed', 'closed_index')):
            with self.subTest(work_state=work_state):
                self.save_note(work_state=work_state, closure_rationale='Only this recorded scope is closed',
                               reopen_conditions=['New contradictory records'])
                for index in range(4):
                    self.case.record('tool_result', dict(tool='forsic_search', path='synthetic.log',
                        search_text='literal-' + str(index), matches=[], coverage_complete=False,
                        scan_exhausted=False, next_cursor={'state': 'retained'}), self.sid)
                resolved = context.resolve_context(**self.kwargs())
                state = current(self.case)
                text, _, _ = context.project_context(resolved, state, max_bytes=3500)
                body = json.loads(text)
                self.assertLessEqual(len(text.encode('utf-8')), 3500)
                item = body[section][0]
                self.assertEqual(item['id'], state['questions'][0]['id'])
                self.assertEqual(item['revision'], self.note['revision'])
                self.assertEqual(item['answer_is_model_assessment'], self.note['answer'])
                self.assertEqual(item['work_state'], work_state)
                self.assertEqual(item['details_call']['arguments']['question_id'], item['id'])
                detail = invoke(self.case, item['details_call']['arguments'])
                answer = invoke(self.case, {**item['details_call']['arguments'],
                    'snapshot_id': detail['snapshot_id'], 'pointer': detail['pointer'] + '/answer'})
                self.assertEqual(answer['text'], self.note['answer'])
                self.assertIn('judgments', item['omitted_fields'])
                self.assertTrue(item['answer_qualifications_omitted'])
                self.assertEqual(body['omitted'][section], 0)
                self.assertGreater(body['omitted']['observed_scope'], 0)

    def test_important_gap_navigation_survives_compaction_and_reads_exact_obligation(self):
        # No keyword classifier: local and human-only obligations use the same
        # existing note contract, and neither is silently answered by projection.
        self.note.update(gaps=['Read the retained scheduler log',
                              'Can the operator supply the approved change record?'],
                         critical_gaps=[], alternatives=[], next_checks=[])
        self.save_note(priority='material', work_state='active')
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('gap-compressed-child', parent=self.sid)
        resolved = context.resolve_context(**self.kwargs('gap-compressed-child'))
        state = current(self.case)
        before = deepcopy(state)
        for work_state in ('active', 'blocked_external', 'scoped_closed'):
            for budget in (3500, 5000):
                with self.subTest(work_state=work_state, budget=budget):
                    candidate = deepcopy(state)
                    candidate['questions'][0]['work_state'] = work_state
                    text, _, _ = context.project_context(resolved, candidate, max_bytes=budget)
                    section = 'closed_index' if work_state == 'scoped_closed' else 'active_questions'
                    item = json.loads(text)[section][0]
                    self.assertLessEqual(len(text.encode()), budget)
                    self.assertEqual(item['answer_is_model_assessment'], self.note['answer'])
                    self.assertEqual(item['remaining_gap_count'], 2)
                    if 'remaining_gap_examples' in item:
                        self.assertEqual(item['remaining_gap_examples'][0]['text'], self.note['gaps'][0])
                        lookup = item['remaining_gap_examples'][0]['read_call']['arguments']
                    else:
                        # Optional previews yield to the current answer under
                        # pressure; its exact state lookup still recovers gaps.
                        self.assertIn('remaining_gap_examples', item['omitted_fields'])
                        detail = invoke(self.case, item['details_call']['arguments'])
                        lookup = detail['gap_lookup']['arguments']
                    self.assertEqual(lookup['question_id'], candidate['questions'][0]['id'])
                    detail = invoke(self.case, lookup)
                    records = [entry['value'] for entry in detail['items']]
                    snapshot = detail['snapshot_id']
                    seen = set()
                    while detail.get('next_call'):
                        call = detail['next_call']['arguments']
                        self.assertEqual(call['question_id'], lookup['question_id'])
                        self.assertEqual(call['snapshot_id'], snapshot)
                        coordinate = (call['pointer'], call['offset'])
                        self.assertNotIn(coordinate, seen)
                        seen.add(coordinate)
                        self.assertLessEqual(len(seen), len(self.note['gaps']))
                        detail = invoke(self.case, call)
                        records.extend(entry['value'] for entry in detail['items'])
                    self.assertEqual(detail['gap_scope']['selected_count'], len(self.note['gaps']))
                    self.assertEqual(len(records), len(self.note['gaps']))
                    self.assertEqual(len({record['id'] for record in records}), len(records))
                    self.assertEqual({g['original_obligation'] for g in records}, set(self.note['gaps']))
                    self.assertTrue(all(g['disposition'] == 'open' for g in records))
        self.assertEqual(state, before)
        self.assertEqual(current(self.case), before)

    def test_foreign_gap_is_not_projected_and_resolved_gap_is_not_reopened(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        for gap in state['gaps']:
            gap['disposition'] = 'resolved'
        foreign = deepcopy(state['gaps'][0])
        foreign.update(id='G-foreign', original_obligation='Foreign human question', disposition='open')
        foreign['question_ref']['id'] = 'Q-foreign'
        state['gaps'].append(foreign)
        before = deepcopy(state)
        text, _, _ = context.project_context(resolved, state)
        item = json.loads(text)['active_questions'][0]
        self.assertEqual(item['remaining_gap_count'], 0)
        self.assertFalse(item.get('remaining_gap_examples'))
        self.assertNotIn('Foreign human question', text)
        self.assertEqual(state, before)

    def test_contextual_open_is_not_an_all_open_completion_gate(self):
        self.save_note(priority='contextual', deferred_reason='Low impact auxiliary detail')
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'ready')
        self.assertEqual(self.validate(prepared)['status'], 'ready')
        self.assertIn('Low impact', prepared['context'])

    def test_priority_unassessed_is_a_declared_classification_gap(self):
        self.save_note(priority='unassessed')
        self.assertIn('importance has not', self.prepare()['reason'])

    def test_closed_unassessed_priority_remains_incomplete_at_prepare_and_validate(self):
        self.save_note(priority='unassessed',work_state='scoped_closed',closure_rationale='Report created',reopen_conditions=['New data'])
        prepared=self.prepare()
        self.assertEqual(prepared['status'],'incomplete')
        self.assertIn('importance has not',prepared['reason'])
        self.assertEqual(self.validate(prepared)['status'],'incomplete')

    def test_unadopted_answer_keeps_limits_and_gaps_in_closed_projection(self):
        self.note['critical_gaps']=['Archive body has not been read']
        self.save_note(priority='material',work_state='scoped_closed',closure_rationale='Caller declared closed',reopen_conditions=['A reader becomes available'])
        result=self.prepare(); body=json.loads(result['context'])
        self.assertIn('Archive body has not been read',result['context'])
        self.assertTrue(body['closed_index'][0]['remaining_gap_count'])
        self.assertEqual(body['closed_index'][0]['judgments'][0]['status'],'candidate')

    def test_uncertain_scoped_closure_can_finish_and_is_indexed(self):
        self.save_note(priority='decision_critical', work_state='scoped_closed',
                       closure_rationale='The supplied records cannot distinguish the competing explanations',
                       reopen_conditions=['A shared identity record becomes available'])
        result = self.prepare()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.validate(result)['status'], 'ready')
        body = json.loads(result['context'])
        self.assertFalse(body['active_questions'])
        self.assertEqual(len(body['closed_index']), 1)
        self.assertFalse(body['completion']['truth_approval'])

    def test_important_deferred_work_is_not_scoped_completion(self):
        for state in ('held', 'blocked_internal', 'blocked_external', 'budget_deferred'):
            with self.subTest(state=state):
                self.save_note(work_state=state, deferred_reason='A required input is missing', reopen_conditions=['Input arrives'])
                self.assertEqual(self.prepare()['status'], 'incomplete')

    def test_changed_answer_stales_done_candidate_but_receipts_do_not(self):
        self.save_note(priority='contextual')
        prepared = self.prepare()
        self.case.record('turn_complete', {'summary': 'Synthetic receipt'}, self.sid)
        self.assertEqual(self.validate(prepared)['status'], 'ready')
        self.note['answer'] = 'Updated analytical interpretation'
        self.save_note(priority='contextual')
        self.assertEqual(self.validate(prepared)['status'], 'stale')

    def test_native_goal_rescope_never_substitutes_other_context(self):
        prepared = self.prepare()
        new_goal = GoalState(goal='Different requested scope', created_at=2.0)
        self.session(self.sid, goal=new_goal)
        result = self.validate(prepared)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('context', result)

    def test_same_case_other_conversation_questions_are_not_injected(self):
        other = GoalState(goal='Independent question', created_at=5.0)
        self.session('other-conversation', goal=other)
        self.bind('other-conversation', self.case)
        result = context.goal_evaluation_context(goal_id=goal_identity(other), **self.kwargs('other-conversation'))
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn(self.note['answer'], result['context'])
        self.assertEqual(json.loads(result['context'])['omitted']['other_goal_or_unbound_questions'], 1)

    def test_native_compression_lineage_resolves_without_creating_binding(self):
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-child', parent=self.sid)
        before = set(self.intake.iterdir())
        result = context.native_goal_binding(**self.kwargs('compressed-child'))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['session_id'], self.sid)
        self.assertEqual(result['requested_session_id'], 'compressed-child')
        self.assertEqual(result['goal_id'], goal_identity(self.goal))
        self.assertEqual(set(self.intake.iterdir()), before)

    def test_branch_without_goal_does_not_borrow_parent_goal(self):
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('branch', parent=self.sid, config={'_branched_from': self.sid})
        result = context.native_goal_binding(**self.kwargs('branch'))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['goal_id'], '')
        compacted = context.post_context_compaction(**self.kwargs('branch'))
        self.assertNotIn(self.note['answer'], compacted['context'])

    def test_focused_session_without_goal_restores_its_own_questions(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM state_meta')
        focused = deepcopy(self.note)
        focused.update(note_id='focused', goal_id='', session_id=self.sid, question='Focused request', answer='Session-owned tentative assessment')
        self.case.record('note', focused, self.sid)
        binding = context.native_goal_binding(**self.kwargs())
        self.assertEqual(binding['status'], 'ready')
        self.assertEqual(binding['goal_id'], '')
        projected = context.post_context_compaction(**self.kwargs())
        self.assertEqual(projected['status'], 'ready')
        self.assertIn('Session-owned tentative assessment', projected['context'])
        self.assertNotIn(self.note['answer'], projected['context'])
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_conflicting_case_binding_on_compression_lineage_is_unavailable(self):
        other, _, _ = fixture(self.intake / 'cases' / 'other')
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-child', parent=self.sid, goal=self.goal)
        self.bind('compressed-child', other)
        result = context.goal_evaluation_context(**self.kwargs('compressed-child'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('conflicting explicit case', result['reason'])

    def test_malformed_binding_is_explicit_unavailable(self):
        (self.intake / (hashlib.sha256(self.sid.encode()).hexdigest()+'.json')).write_text('[]')
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_global_current_pointer_is_never_used(self):
        (self.intake / 'current.json').write_text(json.dumps({'manifest': str(self.case.manifest), 'session_id': self.sid}))
        self.session('unbound', goal=GoalState(goal='other', created_at=3.0))
        self.assertEqual(context.goal_evaluation_context(**self.kwargs('unbound'))['status'], 'not_applicable')

    def test_missing_goal_or_ledger_is_unavailable_not_an_empty_success(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM state_meta')
        self.assertEqual(self.prepare()['status'], 'unavailable')
        self.session(self.sid, goal=self.goal)
        self.case.db.unlink()
        self.assertEqual(self.prepare()['status'], 'unavailable')
        self.assertFalse(self.case.db.exists())

    def test_parent_cycle_is_explicit_failure(self):
        self.session(self.sid, parent='cycle', goal=self.goal)
        self.session('cycle', parent=self.sid)
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_goal_bound_unassessed_results_and_stale_refs_block_completion(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        state['questions'][0]['priority'] = 'contextual'
        state['missions'] = [{'id': 'M-synthetic', 'version': 'v1', 'question_ref': {'kind':'question', 'id':state['questions'][0]['id'], 'version':state['questions'][0]['version']},
                              'state': 'unassessed', 'result_ids': ['retained-result']}]
        self.assertTrue(any('unassessed' in x for x in context.completion_state(resolved, state)))
        state['missions'][0]['state'] = 'completed'
        state['missions'][0]['question_ref']['version'] = 'stale'
        self.assertTrue(any('stale source' in x for x in context.completion_state(resolved, state)))

    def test_planned_mission_restores_id_version_and_next_tool(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case); q = state['questions'][0]
        state['missions'] = [{'id':'M-planned', 'version':'current-mission-version',
            'question_ref':{'kind':'question','id':q['id'],'version':q['version']},
            'state':'draft', 'proposed_tool_name':'forsic_read', 'tool_arguments':{'path':'request.log'},
            'exact_target_scope':'Supplied request record', 'why_it_matters':'Distinguish request from execution'}]
        text, _, _ = context.project_context(resolved, state)
        mission = json.loads(text)['active_missions'][0]
        self.assertEqual(mission['mission_id'], 'M-planned')
        self.assertEqual(mission['mission_version'], 'current-mission-version')
        self.assertEqual(mission['tool'], 'forsic_read')

    def test_failed_actual_result_needs_assessment_and_unavailable_assessment_suffices(self):
        self.save_note(priority='contextual')
        state = current(self.case); question = state['questions'][0]
        owner = {'_goal_id': goal_identity(self.goal), '_session_id': self.sid}
        result = invoke(self.case, {**owner, 'action':'mission', 'payload':{
            'operation_id':'plan-failed-reader', 'question_id':question['id'],
            'expected_revision':question['revision'], 'gap_id':state['gaps'][0]['id'],
            'why_it_matters':'Determine whether the supplied reader can inspect the scope',
            'tool_name':'forsic_read', 'tool_arguments':{'path':'request.log'},
            'inconclusive_rule':'An unsupported read does not establish absence',
            'limits':['No execution attribution'], 'reopen_conditions':['A supported reader becomes available']}})
        mission = result['mission']
        admission = validate_execution_context(self.case, mission['id'], mission['version'],
            'forsic_read', {'path':'request.log'}, goal_id=goal_identity(self.goal), session_id=self.sid)
        started = self.case.record('tool_start', {'tool':'forsic_read', 'arguments':{'path':'request.log'},
                                                'mission_context':admission}, self.sid)
        rid = self.case.record('tool_result', {'tool':'forsic_read', 'started_id':started,
            'mission_context':admission, 'error':'Synthetic unsupported reader', 'error_type':'ValueError'}, self.sid)
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'incomplete')
        self.assertIn('execution failed', prepared['reason'])
        state = current(self.case)
        mission = find(state, 'missions', mission['id']); source = find(state, 'sources', rid)
        resolved = context.resolve_context(**self.kwargs())
        duplicate = deepcopy(state)
        duplicate['missions'][0]['state'] = 'unassessed'
        reasons = context.completion_state(resolved, duplicate)
        self.assertEqual(len(reasons), 1)  # one returned result is not counted twice through Mission/Test
        question = find(state, 'questions', question['id'])
        invoke(self.case, {**owner, 'action':'assess', 'payload':{
            'operation_id':'assess-reader-limitation', 'expected_revision':question['revision'],
            'mission_id':mission['id'], 'mission_version':mission['version'],
            'result_id':rid, 'result_version':source['version'], 'outcome':'unavailable',
            'reasoning_summary':'The failed read establishes a reader limitation, not absence',
            'answer':'The supplied reader cannot inspect this scope; no absence conclusion is drawn',
            'citations':[], 'limitations':['The affected scope remains unknown'],
            'next_check':'Reconsider only when a supported reader is available'}})
        self.assertEqual(self.prepare()['status'], 'ready')  # contextual unresolved scope is still visible

    def test_report_context_observes_freshness_without_becoming_completion_gate(self):
        from forsic_plugin.report_driven.views import bundle
        self.save_note(priority='contextual')
        before = self.prepare()
        saved = bundle(self.case, current(self.case))
        result = self.prepare()
        report = json.loads(result['context'])['reports']
        self.assertEqual(report['bundle_id'], saved['bundle_id'])
        self.assertFalse(report['stale'])
        self.assertFalse(report['artifact_is_completion'])
        self.assertEqual(self.validate(before)['status'], 'stale')
        self.note['answer'] = 'Updated assessment after report'
        self.save_note(priority='contextual')
        result = self.prepare()
        self.assertTrue(json.loads(result['context'])['reports']['stale'])
        self.assertEqual(result['status'], 'ready')  # judge assesses requested deliverable scope

    def test_report_integrity_failure_is_unavailable_not_silently_missing(self):
        from forsic_plugin.report_driven.views import bundle
        saved = bundle(self.case, current(self.case))
        Path(saved['files'][0]).write_text('changed artifact')
        result = self.prepare()
        self.assertEqual(result['status'], 'unavailable')

    def test_projection_is_utf8_bounded_with_explicit_omissions(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        example = state['questions'][0]
        state['questions'] = []
        for i in range(60):
            q = deepcopy(example); q.update(id='Q-' + str(i), question='긴질문' * 500,
                                            answer='긴판단' * 500, alternatives=['반론' * 500])
            state['questions'].append(q)
        text, _, _ = context.project_context(resolved, state)
        self.assertLessEqual(len(text.encode('utf-8')), 5000)
        self.assertGreater(json.loads(text)['omitted']['active_questions'], 0)
        self.assertIn('…', text)

    def test_reads_do_not_change_case_native_files_or_call_network(self):
        paths = [self.case.db, self.case.manifest, self.native, self.intake / (hashlib.sha256(self.sid.encode()).hexdigest()+'.json')]
        before = {p: p.read_bytes() for p in paths}
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')), patch(
                'forsic_plugin.investigation_context.Case', wraps=context.Case) as factory:
            first = self.prepare()
            self.validate(first)
            compacted = context.post_context_compaction(**self.kwargs())
        self.assertEqual(compacted['status'], 'ready')
        self.assertTrue(all(call.kwargs.get('read_only') is True for call in factory.call_args_list))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})


if __name__ == '__main__':
    unittest.main()
