"""Offline synthetic proposal/review/version contracts; no model or Docker I/O."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin import extension_hub as hub
from forsic_plugin.evidence import Case
from forsic_plugin.skill_proposals import WorkflowSkillProposals, receipt_hash
from forsic_plugin.dashboard.management_store import digest


def payload(receipt, name='archive-workflow', version='1.0.0'):
    description = 'Review archive members with retained pagination.'
    return {'reason': 'The successful member selection resolved a reusable pagination problem.',
        'applicability': 'Use when an archive listing is paginated and a member is explicitly selected.',
        'limitations': 'A listing is not proof that member bytes are complete.',
        'receipts': [receipt], 'target': None,
        'draft': {'pack_id': 'local.archive-workflow', 'version': version, 'name': name,
            'description': description, 'requires_tools': ['forsic_archive_read'],
            'skill_md': f'---\nname: {name}\ndescription: {description}\n---\n\n# Archive workflow\n\nUse the retained listing reference and member index. Continue incomplete reads using the returned cursor. Keep the scope and remaining gaps explicit.\n'}}


class WorkflowSkillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.home = self.root / 'home'; self.home.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'HERMES_HOME': str(self.home)}).start()
        evidence = self.root / 'evidence'; evidence.mkdir()
        self.manifest = self.root / 'case.json'
        self.manifest.write_text(json.dumps({'case_id': 'synthetic-case', 'label': 'Synthetic confidential case label',
            'evidence_root': str(evidence), 'output_root': str(self.root / 'output')}))
        self.case = Case(self.manifest)
        start = self.case.record('tool_start', {'tool': 'forsic_archive_read', 'arguments': {'listing_ref': 'selected-source-ref'}}, 'conversation-a')
        self.eid = self.case.record('tool_result', {'tool': 'forsic_archive_read', 'started_id': start,
            'content': 'Private source passage that must remain in the selected case.', 'account': 'synthetic-private-account', 'execution': {'state':'returned','termination_confirmed':True,'exit_code':0}}, 'conversation-a')
        self.manager = WorkflowSkillProposals(self.home)

    def propose(self, **changes):
        request = payload(self.eid); request.update(changes)
        return self.manager.propose(self.case, request, operation_id='propose', allowed_session_ids=['conversation-a'])

    def publish(self, proposal, operation_id='publish'):
        return self.manager.publish(proposal['id'], proposal['draft_sha256'], expected_revision=proposal['revision'], operation_id=operation_id)

    def test_end_to_end_review_hash_publication_idempotency_and_native_pack(self):
        self.assertEqual(self.manager.catalog(), [])
        self.assertFalse(self.manager.base.exists())
        p = self.propose()
        self.assertEqual(p['status'], 'review_pending'); self.assertEqual(hub.list_packs(self.home), [])
        self.assertNotIn('manifest_path', p['origin'])
        self.assertEqual(p['origin']['receipts'][0]['sha256'], receipt_hash(self.case.event(self.eid)))
        self.assertEqual(self.propose()['id'], p['id'])
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.manager.publish(p['id'], '0'*64, expected_revision=p['revision'], operation_id='wrong')
        published = self.publish(p)
        self.assertEqual(published['publication']['status'], 'saved')
        self.assertEqual(published['validation']['structure'], 'verified')
        self.assertEqual(published['validation']['functional'], 'not_observed')
        self.assertEqual(published['activation']['status'], 'not_requested')
        self.assertEqual(hub.selection_snapshot(self.home)['selection'], {'packs': {}})
        pack = hub.load_pack('local.archive-workflow', '1.0.0', self.home, published['publication']['sha256'])
        self.assertEqual(pack['manifest']['tools'], [])
        self.assertEqual(pack['manifest']['dependencies'], [])
        self.assertEqual((Path(pack['path'])/'skills/archive-workflow/SKILL.md').read_text(), p['draft']['skill_md'])
        self.assertEqual(self.publish(p)['publication'], published['publication'])

    def test_changed_draft_requires_current_revision_and_new_hash(self):
        p = self.propose(); draft = deepcopy(p['draft']); draft['skill_md'] += '\nDistinguish retained references from reconstructed content.\n'
        revised = self.manager.update(p['id'], draft, expected_revision=p['revision'], operation_id='edit')
        self.assertNotEqual(revised['draft_sha256'], p['draft_sha256'])
        with self.assertRaisesRegex(ValueError, 'hash changed'): self.publish(p)
        self.assertEqual(self.publish(revised)['status'], 'published')

    def test_rejected_and_withdrawn_fingerprints_are_not_reopened(self):
        for decision in ('reject', 'withdraw'):
            with self.subTest(decision=decision):
                request = payload(self.eid); request['draft']['skill_md'] += '\n' + decision
                p = self.manager.propose(self.case, request, operation_id=decision)
                ended = self.manager.decide(p['id'], decision, expected_revision=p['revision'], operation_id='end-'+decision)
                replay = self.manager.propose(self.case, request, operation_id='again-'+decision)
                self.assertEqual(replay['id'], p['id']); self.assertEqual(replay['status'], ended['status'])
                with self.assertRaisesRegex(ValueError, 'pending'): self.publish(ended)
        self.assertEqual(hub.list_packs(self.home), [])

    def test_rejection_after_edit_suppresses_both_original_and_revised_proposals(self):
        p = self.propose(); request = payload(self.eid)
        request['draft']['skill_md'] += '\nKeep reconstructed content distinct.\n'
        revised = self.manager.update(p['id'], request['draft'], expected_revision=p['revision'], operation_id='revise')
        rejected = self.manager.decide(p['id'], 'reject', expected_revision=revised['revision'], operation_id='reject')
        for candidate in (payload(self.eid), request):
            same = self.manager.propose(self.case, candidate, operation_id='retry-'+digest(candidate)[:10])
            self.assertEqual(same['id'], rejected['id']); self.assertEqual(same['status'], 'rejected')

    def test_origin_scope_hash_and_failed_or_unknown_execution(self):
        request = payload(self.eid)
        with self.assertRaises(PermissionError):
            self.manager.propose(self.case, request, operation_id='other-session', allowed_session_ids=['conversation-b'])
        request['receipts'] = [{'evidence_id': self.eid, 'sha256': '0'*64}]
        with self.assertRaisesRegex(ValueError, 'revision changed'): self.manager.propose(self.case, request, operation_id='stale')
        for result in ({'error':'failed'}, {'success':False}, {'status':'failed'}, {'status':'error'},
                       {'supported':False}, {'coverage':{'state':'partial'}}, {'scan':{'status':'unsupported'}},
                       {'execution':{'state':'completion_unknown'}},
                       {'execution':{'state':'returned','termination_confirmed':False}},
                       {'execution':{'state':'returned','termination_confirmed':True,'exit_code':1}}):
            event = self.case.event(self.eid); data = {**event['data'], **result}
            bad = self.case.record('tool_result', data, 'conversation-a')
            with self.assertRaises(ValueError): self.manager.propose(self.case, payload(bad), operation_id='failed')
        p = self.propose()
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps({**self.case.event(self.eid)['data'], 'content':'changed'}), self.eid))
        with self.assertRaisesRegex(ValueError, 'revision changed'): self.publish(p)
        self.assertEqual(hub.list_packs(self.home), [])

    def test_case_values_credentials_scripts_and_install_commands_never_saved(self):
        values = ['Synthetic confidential case label', 'synthetic-private-account',
                  'Private source passage that must remain in the selected case.', str(self.case.root / 'private/file'),
                  'admin@private.example', '192.0.2.99', 'pip install secret-tool', 'apt-get install unknown-tool',
                  'uv pip install unknown-tool']
        for value in values:
            with self.subTest(value=value):
                request = payload(self.eid); request['draft']['skill_md'] += '\n' + value
                with self.assertRaises(ValueError): self.manager.propose(self.case, request, operation_id='private')
        self.assertEqual(self.manager.catalog(), []); self.assertEqual(hub.list_packs(self.home), [])

    def test_extension_improvement_retains_original_and_detects_target_change(self):
        first = self.publish(self.propose())
        request = payload(self.eid, version='2.0.0')
        request['target'] = {'pack_id':'local.archive-workflow','version':'1.0.0',
                             'sha256':first['publication']['sha256'],'skill_name':'archive-workflow'}
        request['draft']['skill_md'] += '\nRetain the returned member metadata for a later recheck.\n'
        second = self.manager.propose(self.case, request, operation_id='improve')
        selection = hub.selection_snapshot(self.home)
        hub.save_selection({'packs':{'local.archive-workflow':{'version':'1.0.0','sha256':first['publication']['sha256'], 'enabled':False}}}, selection['revision'], 'toggle', self.home)
        with self.assertRaisesRegex(ValueError, 'Target versions or selection changed'): self.publish(second)
        revised = self.manager.update(second['id'], second['draft'], expected_revision=second['revision'], operation_id='review-new-target')
        self.assertNotEqual(second['draft_sha256'], revised['draft_sha256'])
        self.assertEqual(self.publish(revised)['publication']['version'], '2.0.0')
        self.assertEqual(hub.load_pack('local.archive-workflow','1.0.0',self.home)['sha256'], first['publication']['sha256'])

    def test_builtin_improvement_is_bound_review_only_and_no_duplicate_publish(self):
        request = payload(self.eid, name='inspect-artifacts')
        source = Path(__file__).parent/'forsic_plugin/skills/inspect-artifacts/SKILL.md'
        request['target'] = {'builtin':'inspect-artifacts','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
        p = self.manager.propose(self.case, request, operation_id='builtin')
        self.assertIn('review-only', p['publication']['limitation'])
        self.assertIn('reviewed-target/SKILL.md', p['diff'])
        with self.assertRaisesRegex(ValueError, 'review-only'): self.publish(p)
        self.assertEqual(hub.list_packs(self.home), [])

    def test_publication_write_failure_is_not_success_and_retry_uses_same_hash(self):
        p = self.propose()
        with patch.object(hub, 'import_pack', side_effect=OSError('synthetic save failure')):
            with self.assertRaises(OSError): self.publish(p)
        retained = self.manager.get(p['id'])
        self.assertEqual(retained['status'], 'review_pending')
        self.assertEqual(retained['publication']['status'], 'not_saved')
        self.assertEqual(self.publish(p)['status'], 'published')

    def test_import_succeeded_but_management_commit_lost_is_recoverable_without_overwrite(self):
        p = self.propose()
        write = self.manager.store.write
        def fail_proposal_commit(path, value):
            if path == self.manager._path(p['id']): raise OSError('synthetic lost management commit')
            return write(path, value)
        with patch.object(self.manager.store, 'write', side_effect=fail_proposal_commit):
            with self.assertRaises(OSError): self.publish(p)
        installed = hub.load_pack('local.archive-workflow', '1.0.0', self.home)
        self.assertEqual(self.manager.get(p['id'])['status'], 'review_pending')
        retried = self.publish(p)
        self.assertEqual(retried['publication']['sha256'], installed['sha256'])
        self.assertEqual(len(hub.list_packs(self.home)), 1)

    def test_record_tamper_and_scope_switch_do_not_reuse_approval(self):
        p = self.propose(); record = self.manager.get(p['id'])
        record['draft']['skill_md'] += '\nUnreviewed content.\n'
        self.manager.store.write(self.manager._path(p['id']), record)
        with self.assertRaisesRegex(ValueError, 'hash changed'): self.publish(p)
        with patch('forsic_plugin.tenants.current_scope', return_value={'tenant_id':'other','profile_home':str(self.home)}):
            with self.assertRaises(PermissionError): self.manager.catalog()


class WorkflowSkillTenantTests(unittest.TestCase):
    def setUp(self):
        import test_tenant_boundaries
        test_tenant_boundaries.TenantBoundaryTests.setUp(self)

    def test_real_tenant_storage_and_foreign_case_receipts_remain_isolated(self):
        import test_tenant_boundaries
        from forsic_plugin.tenants import bind_scope
        case_a, receipt_a, _ = test_tenant_boundaries.TenantBoundaryTests.case(self, self.a, name='case-a')
        case_b, receipt_b, _ = test_tenant_boundaries.TenantBoundaryTests.case(self, self.b, name='case-b')
        with bind_scope(self.a):
            a = WorkflowSkillProposals(self.a['profile_home'])
            proposal = a.propose(case_a, payload(receipt_a), operation_id='tenant-a')
            self.assertTrue(a.base.is_relative_to(Path(self.a['management_root'])))
        with bind_scope(self.b):
            b = WorkflowSkillProposals(self.b['profile_home'])
            self.assertEqual(b.catalog(), [])
            self.assertFalse(b.base.exists())
            with self.assertRaises(PermissionError): a.catalog()
            with self.assertRaises(PermissionError): b.propose(case_a, payload(receipt_a), operation_id='foreign')
            with self.assertRaises(ValueError): b.propose(case_b, payload(receipt_a), operation_id='foreign-receipt')
            with self.assertRaises(FileNotFoundError): b.get(proposal['id'])
        with bind_scope(self.a):
            published = a.publish(proposal['id'], proposal['draft_sha256'], expected_revision=proposal['revision'], operation_id='publish')
            self.assertEqual(published['status'], 'published')
        with bind_scope(self.b):
            self.assertEqual(hub.list_packs(self.b['profile_home']), [])


if __name__ == '__main__': unittest.main()
