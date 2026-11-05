"""Read-only completion DAG against synthetic native/case DBs and real files."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin import report_work
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.narrative import material
from forsic_plugin.report_driven.template_contract import WRITING_ORDER, REVIEW_CHECKS
from forsic_plugin.report_driven.word_export import export_word
import test_investigation_context as native_fixture
import test_report_narrative as narrative_fixture


CONTRACT = {'required_formats': ['html'], 'layout_required': False, 'completion_target': 'final_ready'}


class ReportCompletionWorkTests(unittest.TestCase):
    def setUp(self):
        report_work._PROJECTION_CACHE.clear()
        self.f = native_fixture.InvestigationContextTests('test_prepare_is_goal_bound_and_marks_model_state_not_evidence')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.case = self.f.case
        self.resolved = resolve_context(**self.f.kwargs())
        self.native = {'_session_id': self.resolved['conversation_id'], '_goal_id': self.resolved['goal_id']}
        self.f.stack.enter_context(patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No provider')))
        self.f.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('No network')))

    def project(self, **options):
        return report_work.goal_work_projection(**self.f.kwargs(),
            goal_identity=[self.f.goal.created_at, self.f.goal.goal],
            completion_contract=options.pop('completion_contract', CONTRACT), **options)

    def author(self):
        q = host.current(self.case)['questions'][0]
        host.invoke(self.case, {'action': 'question', 'payload': {
            'operation_id': 'completion-fixture-assessment', 'question_id': q['id'],
            'expected_revision': q['revision'], 'assessment': 'undetermined',
            'reason': 'Retain the qualified synthetic connection assessment'}, **self.native})
        author = narrative_fixture.ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.case = self.case
        keys = [item['key'] for item in material(host.current(self.case)) if item['kind'] == 'question'
                or item['kind'] == 'gap' and item.get('impact') in ('material', 'decision_critical')]
        for group in WRITING_ORDER:
            packet = host.invoke(self.case, {'action': 'compose', 'section': group, **self.native})
            host.invoke(self.case, {'action': 'compose', 'section': group, 'operation_id': 'author-' + group,
                'edit_ref': packet['save_contract']['edit_ref'], 'question_ids': packet['question_ids'],
                'draft': author.draft(group, packet, keys=keys), **self.native})
        packet = host.invoke(self.case, {'action': 'compose', 'section': 'review', **self.native})
        host.invoke(self.case, {'action': 'compose', 'section': 'review', 'operation_id': 'review-body',
            'edit_ref': packet['save_contract']['edit_ref'], 'question_ids': packet['question_ids'],
            'review': {'summary': 'Synthetic whole document and preserved source comparison',
                'reviewed_node_ids': packet['reviewed_node_ids'], 'answerability_checks': narrative_fixture.answerability_checks(),
                'checks': [{'check': check, 'result': 'satisfied', 'reason': 'Synthetic scope comparison'} for check in REVIEW_CHECKS],
                'comparisons': [{'node_ids': [packet['reviewed_node_ids'][0]], 'basis_keys': [],
                    'statement': 'Qualified account of synthetic observations', 'source_support': 'Common identity unproven',
                    'limits': 'Authorization remains unknown', 'assessment': 'Retain qualified scope'}],
                'findings': []}, **self.native})

    def select(self):
        self.author()
        report = host.invoke(self.case, {'action': 'render', **self.native})
        result = host.invoke(self.case, {'action': 'finalize', 'bundle_id': report['bundle_id'], **self.native})
        self.assertTrue(result['finalized'], result)
        return report

    def test_unknown_policy_and_incomplete_authoring_are_not_zero_remaining_work(self):
        before = self.case.db.read_bytes()
        result = self.project(completion_contract=None)
        self.assertEqual(result['status'], 'ready')
        self.assertIn('required_formats_unknown', result['blockers'])
        self.assertIn('authoring_incomplete', result['blockers'])
        self.assertFalse(result['estimate']['known'])
        self.assertIsNone(result['estimate']['remaining_seconds'])
        self.assertEqual(before, self.case.db.read_bytes())
        self.assertNotEqual(result['artifact_status'], 'final_ready')

    def test_generation_fence_and_failed_canonical_read_are_unknown_without_writes(self):
        before = self.case.db.read_bytes()
        result = report_work.goal_work_projection(**self.f.kwargs(), goal_identity=[99, 'replacement'])
        self.assertEqual(result['blockers'], ['goal_generation_mismatch'])
        with patch('forsic_plugin.report_workspace.selected_basis', side_effect=OSError('private content')):
            result = self.project()
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('private content', json.dumps(result))
        self.assertEqual(before, self.case.db.read_bytes())

    def test_saved_final_is_verified_and_reused_without_claiming_remote_delivery_or_layout(self):
        report = self.select()
        before = self.case.db.read_bytes()
        result = self.project()
        self.assertEqual(result['artifact_status'], 'final_ready', result)
        self.assertEqual(result['artifact_work']['bundle_id'], report['bundle_id'])
        self.assertTrue(result['artifacts'])
        for artifact in result['artifacts']:
            path = Path(artifact['path'])
            self.assertTrue(path.is_absolute())
            self.assertEqual(path.parent, self.case.output / ('report-bundle-' + report['bundle_id']))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])
            self.assertEqual(path.stat().st_size, artifact['bytes'])
        self.assertEqual(result['delivery_status'], 'local_ready')
        self.assertEqual(result['artifact_work']['layout'], 'not_rendered')
        self.assertIsNone(result['artifact_work']['delivery_needed'])
        self.assertEqual(result['digest'], self.project()['digest'])
        self.assertEqual(before, self.case.db.read_bytes())

    def test_new_operation_identical_prose_keeps_current_draft_and_selected_final(self):
        self.select()
        before = self.project()
        author = narrative_fixture.ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.case = self.case
        keys = [item['key'] for item in material(host.current(self.case)) if item['kind'] == 'question'
                or item['kind'] == 'gap' and item.get('impact') in ('material', 'decision_critical')]
        packet = host.invoke(self.case, {'action': 'compose', 'section': 'overall_judgment', **self.native})
        saved = host.invoke(self.case, {'action': 'compose', 'section': 'overall_judgment',
            'operation_id': 'different-operation-identical-prose', 'edit_ref': packet['save_contract']['edit_ref'],
            'question_ids': packet['question_ids'], 'draft': author.draft('overall_judgment', packet, keys=keys), **self.native})
        self.assertTrue(saved['unchanged'])
        after = self.project()
        self.assertEqual(before['basis']['draft_revision'], after['basis']['draft_revision'])
        self.assertEqual(before['basis']['draft_hash'], after['basis']['draft_hash'])
        self.assertEqual(after['artifact_status'], 'final_ready')
        self.assertEqual(before['artifact_work']['selection_receipt'], after['artifact_work']['selection_receipt'])

    def test_required_word_stays_pending_until_actual_cached_file_is_verified(self):
        report = self.select()
        contract = {**CONTRACT, 'required_formats': ['html', 'docx']}
        result = self.project(completion_contract=contract)
        self.assertEqual(result['artifact_work']['export_needed'], ['docx'])
        self.assertNotEqual(result['artifact_status'], 'final_ready')
        exported = export_word(self.case, report['bundle_id'])
        ready = self.project(completion_contract=contract)
        self.assertEqual(ready['artifact_status'], 'final_ready')
        word = next(item for item in ready['artifacts'] if item['path'].endswith('.docx'))
        self.assertEqual(Path(word['path']), exported['path'])
        self.assertEqual(hashlib.sha256(exported['path'].read_bytes()).hexdigest(), word['sha256'])
        cache_root = self.case.output / '.word-exports'
        moved = self.case.output / 'moved-word-exports'
        cache_root.rename(moved); cache_root.symlink_to(moved, target_is_directory=True)
        try:
            self.assertNotEqual(self.project(completion_contract=contract)['artifact_status'], 'final_ready')
        finally:
            cache_root.unlink(); moved.rename(cache_root)
        exported['path'].write_bytes(b'corrupt cached export')
        result = self.project(completion_contract=contract)
        self.assertNotEqual(result['artifact_status'], 'final_ready')
        self.assertEqual(result['artifact_work']['integrity'], 'unavailable')

    def test_partial_and_preserved_draft_have_distinct_native_artifact_status(self):
        self.assertEqual(self.project()['artifact_status'], 'partial_checkpoint')
        self.author()
        self.assertEqual(self.project()['artifact_status'], 'draft_preserved')
        self.assertEqual(self.project()['artifacts'], [])

    def test_latest_real_selection_beats_earlier_lexical_bundle(self):
        from forsic_plugin.report_driven.finalization import finalize
        from forsic_plugin.report_driven.views import bundle
        self.author()
        state = host.current(self.case)
        ids = [q['id'] for q in state['questions']]
        scope = {'session_id': self.resolved['conversation_id'], 'goal_id': self.resolved['goal_id']}
        first = bundle(self.case, state, question_ids=ids, report_scope=scope)
        second = bundle(self.case, state, question_ids=ids, report_scope=scope,
                        redact=['literal-not-present-in-synthetic-evidence'])
        earlier, latest = sorted((first, second), key=lambda item: item['bundle_id'])
        self.assertNotEqual(earlier['bundle_id'], latest['bundle_id'])
        self.assertTrue(finalize(self.case, state, earlier['bundle_id'], report_scope=scope)['finalized'])
        selection = finalize(self.case, state, latest['bundle_id'], report_scope=scope)
        self.assertTrue(selection['finalized'], selection)
        projected = self.project()
        self.assertEqual(projected['artifact_status'], 'final_ready')
        self.assertEqual(projected['artifact_work']['bundle_id'], latest['bundle_id'])
        self.assertEqual(projected['artifact_work']['selection_receipt'], selection['finalization_receipt'])

    def test_actually_selected_availability_disclosure_is_limited_final_and_cannot_replace_executive_review(self):
        from forsic_plugin import independent_review as review
        from forsic_plugin.report_driven.finalization import finalize
        from forsic_plugin.report_driven.views import bundle
        self.author()
        state = host.current(self.case)
        ids = [q['id'] for q in state['questions']]
        scoped = review.report_review_scope(self.resolved, ids)
        context = {'enabled': True, 'resolved': scoped}
        scope = {'session_id': self.resolved['conversation_id'], 'goal_id': self.resolved['goal_id']}
        retained = {'status': 'unavailable', 'review_id': 'settled-final', 'latest_stage': 'final',
            'final_review_current': False, 'latest_final_review_id': 'settled-final',
            'latest_final_status': 'unavailable', 'pending_finding_ids': [], 'requires_followup': False,
            'dispositions': [], 'stale_review_ids': [], 'historical_review_ids': []}
        # Existing gate behavior is locally simulated; bundle hashes and actual
        # selected receipt are real, with no provider call or gate replacement.
        with patch.object(review, 'review_state', return_value=retained):
            report = bundle(self.case, state, question_ids=ids, review_context=context, report_scope=scope)
            selection = finalize(self.case, state, report['bundle_id'], review_context=context, report_scope=scope)
            self.assertTrue(selection['finalized'], selection)
            result = self.project(enabled=True)
            self.assertEqual(result['artifact_status'], 'final_ready_with_limitations', result)
            self.assertEqual(result['artifact_work']['availability_status'], 'unavailable')
            self.assertEqual(result['limitations'][0]['description'], result['artifact_work']['availability_limitation'])
            self.assertTrue(result['limitations'][0]['description'])
            self.assertEqual(result['artifact_work']['selection_receipt'], selection['finalization_receipt'])
            blocked = self.project(enabled=True, executive_followup=True)
            self.assertEqual(blocked['artifact_status'], 'draft_preserved')
            self.assertIn('executive_dialogue_incomplete', blocked['blockers'])
            self.assertFalse(blocked['artifact_work']['review_satisfied'])

    def test_corrupt_file_and_missing_snapshot_cannot_reuse_selected_final(self):
        report = self.select()
        root = self.case.output / ('report-bundle-' + report['bundle_id'])
        path = root / 'integrated.html'
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        self.assertNotEqual(self.project()['artifact_status'], 'final_ready')
        path.write_bytes(original)
        snapshot = self.case.output / ('report-state-' + report['snapshot_id'] + '.json')
        snapshot.unlink()
        self.assertEqual(self.project()['artifact_work']['integrity'], 'unavailable')

    def test_executive_requirement_and_pending_judgment_survive_projection(self):
        result = self.project(enabled=True, executive_followup=True)
        self.assertEqual(result['review_work']['remaining_rounds'], 3)
        self.assertEqual(result['quota']['ordinary']['required_reserved'], 1)
        self.assertIn('executive_dialogue_incomplete', result['blockers'])
        before = deepcopy(result['basis'])
        self.f.save_note(judgment_review_required=True)
        after = self.project(enabled=True, executive_followup=True)
        self.assertIn('current_judgment_review_required', after['blockers'])
        self.assertNotEqual(before['judgment_revision'], after['basis']['judgment_revision'])

    def test_action_classification_uses_actual_scope_and_work_not_a_report_label(self):
        result = self.project()
        classify = lambda tool, arguments: report_work.classify_action(result, {'tool': tool, 'arguments': arguments})
        self.assertEqual(classify('forsic_reporting', {'action': 'compose'})['purpose'], 'read')
        self.assertEqual(classify('forsic_reporting', {'action': 'compose', 'draft': {}})['purpose'], 'report')
        self.assertEqual(classify('forsic_reporting', {'action': 'compose', 'question_ids': ['foreign']})['purpose'], 'discovery')
        self.assertEqual(classify('forsic_shell', {'purpose': 'report', 'command': 'broad search'})['purpose'], 'discovery')
        self.assertEqual(classify('forsic_reporting', {'action': 'source', 'source_id': 'foreign'})['purpose'], 'discovery')
        source_id = result['basis']['source_ids'][0]
        self.assertEqual(classify('forsic_reporting', {'action': 'source', 'source_id': source_id})['purpose'], 'read')
        hook = self.project(mode='classify_action', action={'tool': 'forsic_reporting', 'arguments': {'action': 'compose'}})
        self.assertEqual(hook['purpose'], 'read')
        self.assertEqual(hook['projection_digest'], result['digest'])

    def test_pinned_review_mismatch_and_required_unverified_layout_are_explicit(self):
        self.select()
        result = self.project(completion_contract={**CONTRACT, 'required_review': True})
        self.assertIn('review_policy_configuration_mismatch', result['blockers'])
        self.assertNotEqual(result['artifact_status'], 'final_ready')
        result = self.project(completion_contract={**CONTRACT, 'layout_required': True})
        self.assertTrue(result['artifact_work']['layout_needed'])
        self.assertNotEqual(result['artifact_status'], 'final_ready')

    def test_exact_saved_shell_mission_is_not_a_fabricated_narrow_output_binding(self):
        from forsic_plugin.report_driven.investigation_state import validate_execution_context
        from forsic_plugin.shell_workspace import MAX_OUTPUT_BYTES, validate
        q = host.current(self.case)['questions'][0]
        arguments = {'action': 'run', 'command': 'wc -c /evidence/request.log',
                     'cwd': '/work', 'timeout_seconds': 30}
        mission = host.invoke(self.case, {'action': 'mission', 'payload': {
            'operation_id': 'saved-shell-plan', 'question_id': q['id'], 'expected_revision': q['revision'],
            'gap_id': q['remaining_gap_ids'][0], 'tool_name': 'forsic_shell', 'tool_arguments': arguments,
            'why_it_matters': 'Inspect the exact retained synthetic file for the current question',
            'inconclusive_rule': 'Byte count cannot establish execution or common identity',
            'limits': ['No actor attribution'], 'reopen_conditions': ['Independent matching record']}, **self.native})['mission']
        # Existing admission really binds these exact inputs and current owner.
        context = validate_execution_context(self.case, mission['id'], mission['version'], 'forsic_shell',
            arguments, goal_id=self.resolved['goal_id'], session_id=self.resolved['conversation_id'])
        self.assertEqual(context['mission_id'], mission['id'])
        self.assertEqual(validate(arguments)['timeout_seconds'], 30)
        self.assertGreater(MAX_OUTPUT_BYTES, 64000)  # Actual capture cap, not the small UI preview.
        self.assertEqual(mission['budget']['authority'], 'unallocated')
        before = self.case.db.read_bytes()
        action = {'tool': 'forsic_shell', 'arguments': {**arguments, 'mission_id': mission['id'],
            'mission_version': mission['version'], '_native_action': {'purpose': 'targeted_verification',
                'scope': {'targets': ['/evidence/request.log']}, 'blocker': mission['gap_ref']['id'],
                'max_output_bytes': 64000, 'max_seconds': 30, 'expected_result': 'selected file byte count'}}}
        result = report_work.classify_action(self.project(), action)
        self.assertEqual(result['purpose'], 'discovery')
        self.assertNotIn('verification_binding', result)
        self.assertEqual(before, self.case.db.read_bytes())

    def static_mission(self):
        self.f.save_note(judgment_review_required=True)
        state = host.current(self.case)
        q = state['questions'][0]
        gap = next(g for g in state['gaps'] if g['id'] in q['remaining_gap_ids']
                   and g['impact'] in ('material', 'decision_critical'))
        arguments = {'action': 'static_scan', 'paths': ['/work/selected.log'], 'mode': 'literal',
                     'text': 'marker', 'max_bytes': 12, 'max_matches': 1,
                     'max_seconds': 2, 'timeout_seconds': 30}
        mission = host.invoke(self.case, {'action': 'mission', 'payload': {
            'operation_id': 'bounded-static-plan', 'question_id': q['id'], 'expected_revision': q['revision'],
            'gap_id': gap['id'], 'tool_name': 'forsic_shell', 'tool_arguments': arguments,
            'support_rule': 'An exact marker supports presence only in the selected derivative',
            'why_it_matters': 'Resolve the current material gap before updating the current judgment',
            'inconclusive_rule': 'A bounded no-match leaves unscanned bytes unresolved',
            'limits': ['No execution or actor attribution'],
            'reopen_conditions': ['A changed retained derivative']}, **self.native})['mission']
        return {'tool': 'forsic_shell', 'arguments': {**arguments,
            'mission_id': mission['id'], 'mission_version': mission['version']}}, mission

    def test_current_discriminating_static_mission_uses_enforced_bounds_and_read_only_hook(self):
        from forsic_plugin.shell_workspace import validate, MAX_OUTPUT_BYTES
        from forsic_plugin.static_scan import scan
        action, mission = self.static_mission()
        before = self.case.db.read_bytes()
        result = self.project(mode='classify_action', action=action)
        self.assertEqual(result['purpose'], 'targeted_verification', result)
        binding = result['verification_binding']
        self.assertEqual(binding['arguments_sha256'], hashlib.sha256(json.dumps(action['arguments'],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(binding['blocker'], 'current_judgment_review_required')
        self.assertIn(binding['blocker'], self.project()['blockers'])
        self.assertEqual(binding['scope']['mission_id'], mission['id'])
        self.assertEqual(binding['scope']['targets'], ['/work/selected.log'])
        self.assertEqual((binding['max_seconds'], binding['max_output_bytes']), (30, MAX_OUTPUT_BYTES))
        self.assertEqual((binding['max_input_bytes'], binding['max_matches'], binding['scan_seconds']), (12, 1, 2))
        # Execute the existing local scanner, not Docker or an evidence file.
        args = validate(mission['tool_arguments'])
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'selected.log').write_bytes(b'marker ' * 100)
            Path(root, 'unselected.log').write_bytes(b'marker' * 100)
            scanned = scan({k: v for k, v in args.items() if k != 'timeout_seconds'}, root=root)
        self.assertEqual([item['path'] for item in scanned['files']], ['/work/selected.log'])
        self.assertLessEqual(sum(item['captured_bytes'] for item in scanned['files']), 12)
        self.assertLessEqual(sum(len(item['matches']) for item in scanned['files']), 1)
        self.assertEqual(before, self.case.db.read_bytes())

    def test_static_mission_changed_input_stale_version_or_resolved_judgment_is_not_closing_verification(self):
        action, _ = self.static_mission()
        for replacement in ({'paths': ['/work/other.log']}, {'mission_version': 'obsolete'},
                            {'command': 'arbitrary command'}, {'timeout_seconds': 300}):
            with self.subTest(replacement=replacement):
                changed = {**action, 'arguments': {**action['arguments'], **replacement}}
                self.assertEqual(self.project(mode='classify_action', action=changed)['purpose'], 'discovery')
        self.f.save_note(judgment_review_required=False)
        self.assertEqual(self.project(mode='classify_action', action=action)['purpose'], 'discovery')

    def test_unchanged_cache_reuses_projection_and_cannot_be_mutated_by_consumer(self):
        before = self.case.db.read_bytes()
        with patch.object(report_work, 'project', wraps=report_work.project) as compute:
            first = self.project()
            first['blockers'].append('caller-injected')
            second = self.project()
            self.assertEqual(compute.call_count, 1)
            self.assertNotIn('caller-injected', second['blockers'])
            different = self.project(completion_contract={**CONTRACT, 'required_formats': ['html', 'docx']})
            self.assertEqual(compute.call_count, 2)
            self.assertEqual(different['policy']['required_formats'], ['docx', 'html'])
        self.assertEqual(before, self.case.db.read_bytes())

    def test_cache_recomputes_after_current_question_revision_and_new_review_intent(self):
        from forsic_plugin import independent_review as review
        with patch.object(report_work, 'project', wraps=report_work.project) as compute:
            first = self.project(enabled=True)
            self.f.save_note(judgment_review_required=True)
            changed = self.project(enabled=True)
            self.assertEqual(compute.call_count, 2)
            self.assertNotEqual(first['basis']['judgment_revision'], changed['basis']['judgment_revision'])
            self.assertIn('current_judgment_review_required', changed['blockers'])
            request = {'request_id': 'cache-intent', 'identity': review._identity(self.resolved),
                'created_ns': 1, 'schema_version': 3, 'stage': 'checkpoint',
                'material_fingerprint': review.freshness(self.resolved)['fingerprint']}
            path = self.case.output / ('independent-review-' + review._goal_key(self.resolved) + '-cache-intent-request.json')
            review._immutable(path, request)
            after_intent = self.project(enabled=True)
            self.assertEqual(compute.call_count, 3)
            self.assertEqual(after_intent['quota']['ordinary']['used'], 1)
            review._immutable(path.with_name(path.name.replace('-request.json', '-response.json')),
                {'request_id': 'cache-intent', 'status': 'cancelled'})
            self.project(enabled=True)
            self.assertEqual(compute.call_count, 4)

    def test_cache_artifact_same_size_and_restored_mtime_still_invalidates_on_ctime(self):
        report = self.select()
        path = self.case.output / ('report-bundle-' + report['bundle_id']) / 'integrated.html'
        original = path.read_bytes();info = path.stat()
        with patch.object(report_work, 'project', wraps=report_work.project) as compute:
            self.assertEqual(self.project()['artifact_status'], 'final_ready')
            self.assertEqual(self.project()['artifact_status'], 'final_ready')
            self.assertEqual(compute.call_count, 1)
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            changed = self.project()
            self.assertEqual(compute.call_count, 2)
            self.assertNotEqual(changed['artifact_status'], 'final_ready')
            self.assertEqual(changed['artifact_work']['integrity'], 'unavailable')

    def test_cache_dependency_read_failure_never_reuses_ready_result(self):
        self.select()
        self.assertEqual(self.project()['artifact_status'], 'final_ready')
        with (patch.object(report_work, '_cache_stamp', side_effect=OSError('dependency metadata unavailable')),
              patch.object(report_work, 'project', side_effect=OSError('current authority unavailable'))):
            self.assertEqual(self.project()['status'], 'unknown')

    def test_inflight_review_is_not_cached_from_only_filesystem_metadata(self):
        actual = report_work.project
        def pending(*args, **kwargs):
            result = actual(*args, **kwargs)
            result['review_work'].update(status='stale', inflight_review_ids=['live-worker'])
            return result
        with patch.object(report_work, 'project', side_effect=pending) as compute:
            self.project(); self.project()
            self.assertEqual(compute.call_count, 2)


if __name__ == '__main__':unittest.main()
