"""Offline native accepted-human continuation after a hard runtime stop."""
from contextlib import ExitStack, nullcontext
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hermes_cli import goals, goals_runtime
from hermes_state import SessionDB
from tui_gateway import methods_prompt


class NativeRuntimeFollowupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        home = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME':home}))
        self.db = SessionDB(Path(home)/'state.db'); self.stack.callback(self.db.close)
        self.db.create_session('dialogue', source='web')
        self.stack.enter_context(patch.object(goals, '_get_session_db', return_value=self.db))
        self.stack.enter_context(patch.object(goals, 'configured_runtime_budget', return_value=120.0))
        self.stack.enter_context(patch.object(goals_runtime, 'configured_runtime_budget', return_value=120.0))
        self.stack.enter_context(patch.object(goals, 'judge_goal', Mock(side_effect=AssertionError('No model calls'))))
        self.server = SimpleNamespace(register_method=lambda *args:None,
            _load_cfg=lambda:{'goals':{'max_turns':8}},
            _session_profile_runtime_scope=lambda _:nullcontext(), _session_db=lambda _:nullcontext(self.db),
            _submit_row_owner_key=lambda staged, session:staged['_submit_row_session_id'],
            logger=logging.getLogger(__name__))
        methods_prompt.register(self.server)
        self.previous = goals.GoalState('Prior report', status='paused', created_at=1000.,
            paused_reason=goals_runtime.RUNTIME_BUDGET_REASON, runtime_budget_seconds=100.,
            runtime_elapsed_seconds=100., runtime_checkpoints=[{'threshold_seconds':43200,
                'decision':'finish','source_message_id':'prior-saved-checkpoint'}])
        self.raw = self.previous.to_json(); self.db.set_meta('goal:dialogue', self.raw)
        self.text = 'Regenerate the selected report, retain its hypotheses and finish the existing review.'
        self.row_id = self.db.append_message('dialogue','user',content=self.text)
        self.session = {'source':'web','session_key':'dialogue','agent':SimpleNamespace(session_id='dialogue'),
            'history_lock':threading.RLock(), '_submit_user_row':{'_row_id':self.row_id,'content':self.text,
                '_submit_row_session_id':'dialogue'}}

    def start(self, **kwargs):
        return self.server._start_runtime_handoff_followup(self.session, self.text, **kwargs)

    def test_fresh_human_creates_new_bounded_goal_and_keeps_exact_original_runtime(self):
        receipt = self.start(fresh_human=True)
        current = goals.load_goal('dialogue')
        self.assertEqual(receipt['previous_goal_state'], json.loads(self.raw))
        self.assertEqual(current.runtime_followup_of, goals.goal_identity(self.previous))
        self.assertNotEqual(goals.goal_identity(current), goals.goal_identity(self.previous))
        self.assertEqual(current.runtime_followup_user_row_id, self.row_id)
        self.assertEqual(current.goal, self.text)
        self.assertEqual(current.runtime_budget_seconds, 120.0)
        row = self.db.get_messages('dialogue')[0]
        self.assertEqual(row['content'], self.text)
        self.assertIsNone(self.start(fresh_human=True))

    def test_automatic_background_and_manual_pauses_cannot_extend_the_old_goal(self):
        for source, fresh, reason in [('web',False,goals_runtime.RUNTIME_BUDGET_REASON),
            ('cron',True,goals_runtime.RUNTIME_BUDGET_REASON),('tool',True,goals_runtime.RUNTIME_BUDGET_REASON),
            ('web',True,'user-paused'),('web',True,'user-safe-paused')]:
            with self.subTest(source=source, fresh=fresh, reason=reason):
                self.session['source'] = source
                self.previous.paused_reason = reason
                before = self.previous.to_json(); self.db.set_meta('goal:dialogue', before)
                self.assertIsNone(self.start(fresh_human=fresh))
                self.assertEqual(self.db.get_meta('goal:dialogue'), before)

    def test_accepted_row_scope_and_exact_parent_cas_are_required(self):
        self.session['_submit_user_row']['content'] = 'Different message'
        with self.assertRaises(goals._StaleGoalEvaluation):self.start(fresh_human=True)
        self.assertEqual(self.db.get_meta('goal:dialogue'), self.raw)
        self.session['_submit_user_row']['content'] = self.text
        original_save = goals.GoalManager._save
        replacement = goals.GoalState('Different user objective', created_at=2000.).to_json()
        def raced(manager):
            self.db.set_meta('goal:dialogue', replacement)
            return original_save(manager)
        with patch.object(goals.GoalManager, '_save', raced):
            with self.assertRaises(goals._StaleGoalEvaluation):self.start(fresh_human=True)
        self.assertEqual(self.db.get_meta('goal:dialogue'), replacement)

    def test_same_physical_user_row_cannot_grant_two_runtime_allowances(self):
        self.start(fresh_human=True)
        current = goals.load_goal('dialogue')
        current.status, current.runtime_started_at = 'paused', None
        current.paused_reason = goals_runtime.RUNTIME_BUDGET_REASON
        before = current.to_json(); self.db.set_meta('goal:dialogue',before)
        with self.assertRaises(goals._StaleGoalEvaluation):self.start(fresh_human=True)
        self.assertEqual(self.db.get_meta('goal:dialogue'), before)


if __name__ == '__main__':
    unittest.main()
