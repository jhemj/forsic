"""Actual registered tool path, using temporary synthetic case receipts only."""
import json
from unittest.mock import Mock, patch
import unittest

from forsic_plugin import register
from forsic_plugin import extension_hub as hub
from forsic_plugin.capability_schemas import schema_overrides
import test_skill_proposals as proposals_fixture
import test_extension_hub as extension_fixture
from test_skill_proposals import payload


class NativeSkillProposalTests(unittest.TestCase):
    def setUp(self):
        proposals_fixture.WorkflowSkillTests.setUp(self)
        self.binding = {'status': 'ready', 'case_id': 'synthetic-case',
            'conversation_id': 'conversation-a', 'session_lineage': ['compressed-session', 'conversation-a']}
        patch('forsic_plugin.intake.Intake.case', return_value=self.case).start()
        patch('forsic_plugin.investigation_context.native_goal_binding', return_value=self.binding).start()
        ctx = Mock()
        ctx.get_config.side_effect = lambda key: str(self.root/'intake') if key == 'intake_root' else None
        register(ctx)
        self.tool = next(call.kwargs for call in ctx.register_tool.call_args_list if call.kwargs['name'] == 'forsic_skill')

    def call(self, args):
        return json.loads(self.tool['handler'](args, session_id='compressed-session'))

    def test_real_handler_persists_idempotent_private_draft_without_publication(self):
        request = {'action': 'propose', 'proposal': payload(self.eid)}
        result = self.call(request)
        self.assertEqual(result['status'], 'review_pending', result)
        self.assertEqual(self.call(request)['id'], result['id'])
        record = self.manager.get(result['id'])
        self.assertEqual(record['origin']['receipts'][0]['session_id'], 'conversation-a')
        self.assertEqual(hub.list_packs(self.home), [])
        self.assertNotIn('draft', result)
        self.assertNotIn('Private source passage', json.dumps(result))
        listed = self.call({'action': 'proposals'})
        self.assertEqual(listed['total'], 1)
        self.assertEqual(listed['proposals'][0]['id'], result['id'])
        self.assertNotIn('skill_md', json.dumps(listed))

    def test_binding_failure_or_foreign_conversation_never_saves(self):
        self.binding['session_lineage'] = ['foreign']
        self.binding['conversation_id'] = 'foreign'
        result = self.call({'action': 'propose', 'proposal': payload(self.eid)})
        self.assertEqual(result['status'], 'not_saved')
        self.assertEqual(self.manager.catalog(), [])
        self.binding['case_id'] = 'other-case'
        self.assertEqual(self.call({'action': 'proposals'})['status'], 'not_saved')

    def test_catalog_read_and_filtered_schema_keep_proposal_contract(self):
        self.assertIn('skills', self.call({}))
        self.assertIn('skills', self.call({'action': 'read'}))
        with patch('tools.skills_tool.skill_view', return_value='{"success":true,"content":"procedure"}'):
            result = self.call({'name': 'forensic-investigation'})
        self.assertEqual(result['procedure_ref']['builtin'], 'forensic-investigation')
        self.assertEqual(len(result['procedure_ref']['sha256']), 64)
        policy = Mock(); policy.skill_enabled.return_value = False
        filtered = schema_overrides('forsic_skill', self.tool['schema'], policy)['parameters']['properties']
        self.assertIn('proposal', filtered)
        self.assertNotIn('name', filtered)
        self.assertNotIn('publish', filtered['action']['enum'])
        self.assertIn('error', self.call({'action': 'publish'}))


class PublishedWorkflowNativeReadTests(unittest.TestCase):
    setUp = extension_fixture.ExtensionNativeTests.setUp
    menu = extension_fixture.ExtensionNativeTests.menu

    def test_proposal_review_selection_and_actual_native_reader_share_exact_version(self):
        from tools.registry import registry
        from forsic_plugin.skill_proposals import WorkflowSkillProposals
        from forsic_plugin.extension_runtime import refresh_extensions, runtime_name
        case = self.cases['case-a']
        started = case.record('tool_start', {'tool':'forsic_archive_read','arguments':{}}, 'case-a')
        receipt = case.record('tool_result', {'tool':'forsic_archive_read','started_id':started,
            'content':'Synthetic retained input with no procedure content.'}, 'case-a')
        draft = payload(receipt)
        response = json.loads(registry.dispatch('forsic_skill', {'action':'propose','proposal':draft}, session_id='case-a'))
        self.assertEqual(response['status'], 'review_pending', response)
        proposals = WorkflowSkillProposals(self.home)
        record = proposals.get(response['id'])
        published = proposals.publish(record['id'], record['draft_sha256'],
            expected_revision=record['revision'], operation_id='reviewed-native-procedure')
        publication = published['publication']
        pack = hub.load_pack(draft['draft']['pack_id'],draft['draft']['version'],self.home,publication['sha256'])
        name = runtime_name(pack,pack['manifest']['skills'][0])
        refresh_extensions(self.home)
        self.assertNotIn(name,self.menu('case-a')['forsic_skill']['parameters']['properties']['name']['enum'])
        self.selections['case-a']['packs'][pack['manifest']['id']] = {
            'version':pack['manifest']['version'],'sha256':pack['sha256'],'enabled':True,'tools':{},'skills':{}}
        self.assertIn(name,self.menu('case-a')['forsic_skill']['parameters']['properties']['name']['enum'])
        self.assertNotIn(name,self.menu('case-b')['forsic_skill']['parameters']['properties']['name']['enum'])
        with patch('tools.skills_tool_plugin._preprocess_skill',side_effect=AssertionError('read only')):
            body = json.loads(registry.dispatch('forsic_skill',{'name':name},session_id='case-a'))
        self.assertTrue(body['success'],body)
        self.assertIn('Continue incomplete reads using the returned cursor.',body['content'])
        self.assertEqual(body['procedure_ref'],{'pack_id':pack['manifest']['id'],
            'version':pack['manifest']['version'],'sha256':pack['sha256'],'skill_name':draft['draft']['name']})
        self.assertNotIn('Synthetic retained input',body['content'])


if __name__ == '__main__': unittest.main()
