"""Review and activation through real HTTP/local stores; runtime callbacks stubbed."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from forsic_plugin import extension_hub as hub
from forsic_plugin.dashboard import management_api as api
import test_management_api as management
from test_skill_proposals import payload


class WorkflowSkillAPITests(unittest.TestCase):
    setUp = management.ManagementAPITests.setUp
    get = management.ManagementAPITests.get
    action = management.ManagementAPITests.action
    case = management.ManagementAPITests.case
    register = management.ManagementAPITests.register

    def proposal(self):
        case = self.case()
        start = case.record('tool_start', {'tool':'forsic_archive_read','arguments':{}}, 'case-session')
        receipt = case.record('tool_result', {'tool':'forsic_archive_read','started_id':start,'content':'Synthetic private result paragraph.'}, 'case-session')
        response = self.action('skill.propose', case_id='case-a', proposal=payload(receipt))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['proposal'], case

    def publish(self, proposal, **kwargs):
        response = self.action('skill.publish', case_id='case-a', proposal_id=proposal['id'],
            reviewed_sha256=proposal['draft_sha256'], proposal_revision=proposal['revision'], **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['proposal']

    def test_propose_edit_review_publish_activate_real_pack_and_idle_runtime(self):
        proposal, case = self.proposal()
        self.assertEqual(len(self.get('case-a')['skill_proposals']['items']), 1)
        self.assertEqual(hub.list_packs(self.home), [])
        self.refreshed.assert_not_called()
        draft = deepcopy(proposal['draft'])
        draft['skill_md'] += '\n```bash\nwc -l /work/${input}\n```\nThe relevant system logs may be under /var/log.\n'
        changed = self.action('skill.draft', case_id='case-a', proposal_id=proposal['id'],
                              draft=draft, proposal_revision=proposal['revision'])
        self.assertEqual(changed.status_code, 200, changed.text)
        revised = changed.json()['proposal']
        stale = self.action('skill.publish', case_id='case-a', proposal_id=proposal['id'],
                            reviewed_sha256=proposal['draft_sha256'], proposal_revision=revised['revision'])
        self.assertEqual(stale.status_code, 409)
        published = self.publish(revised, operation_id='publish-skill')
        self.assertEqual(published['activation']['status'], 'not_requested')
        self.assertEqual(hub.selection_snapshot(self.home)['selection']['packs'], {})
        self.assertEqual(self.publish(revised, operation_id='publish-skill')['publication'], published['publication'])
        self.refreshed.assert_not_called()
        activated = self.action('skill.activate', case_id='case-a', proposal_id=published['id'],
                                proposal_revision=published['revision'], scope='default')
        self.assertEqual(activated.status_code, 200, activated.text)
        selected = hub.selection_snapshot(self.home)['selection']['packs']['local.archive-workflow']
        self.assertTrue(selected['enabled'])
        self.assertEqual(selected['sha256'], published['publication']['sha256'])
        self.assertEqual(activated.json()['proposal']['activation']['scope'], 'default')
        self.assertIn(activated.json()['proposal']['activation']['status'], ('applied','pending'))
        self.assertTrue(self.refreshed.called)
        self.assertEqual(activated.json()['proposal']['validation']['functional'], 'not_observed')

    def test_case_activation_stays_pinned_and_busy_runtime_reports_pending(self):
        proposal, case = self.proposal()
        environment = self.register()
        self.manager.select_case(case, environment, extensions={'packs':{}}, expected_revision=0,
                                 operation_id='initial-pin', sessions_safe=lambda:True)
        published = self.publish(proposal)
        with patch.object(api.runtime, '_durable_idle', return_value=False):
            activated = self.action('skill.activate', case_id='case-a', proposal_id=published['id'],
                                    proposal_revision=published['revision'], scope='case')
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()['proposal']['activation']['status'], 'pending')
        state = self.manager.case_state(case)
        self.assertEqual(state['current']['extensions'], {'packs':{}})
        self.assertEqual(state['pending']['extensions']['packs']['local.archive-workflow']['sha256'], published['publication']['sha256'])
        self.assertEqual(hub.selection_snapshot(self.home)['selection']['packs'], {})

    def test_unpublished_rejected_cross_case_and_remote_write_are_denied(self):
        proposal, case = self.proposal()
        activate = self.action('skill.activate', case_id='case-a', proposal_id=proposal['id'],
                               proposal_revision=proposal['revision'], scope='default')
        self.assertEqual(activate.status_code, 409)
        rejected = self.action('skill.reject', case_id='case-a', proposal_id=proposal['id'], proposal_revision=proposal['revision'])
        self.assertEqual(rejected.status_code, 200, rejected.text)
        ended = rejected.json()['proposal']
        publish = self.action('skill.publish', case_id='case-a', proposal_id=proposal['id'],
                              reviewed_sha256=ended['draft_sha256'], proposal_revision=ended['revision'])
        self.assertEqual(publish.status_code, 409)
        wrong_case = self.action('skill.withdraw', proposal_id=proposal['id'], proposal_revision=ended['revision'])
        self.assertEqual(wrong_case.status_code, 409)
        with patch('forsic_plugin.setup.local_admin', return_value=False):
            remote = self.action('skill.withdraw', case_id='case-a', proposal_id=proposal['id'], proposal_revision=ended['revision'])
        self.assertEqual(remote.status_code, 403)
        self.assertEqual(hub.list_packs(self.home), [])


if __name__ == '__main__': unittest.main()
