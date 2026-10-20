import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from telegram_actions import ChatActions, case_is_working, working_topics


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_only_when_active_throttled_and_stops_without_message(self):
        calls, active, now = [], {7}, [10.0]
        class Bot:
            async def send_chat_action(self, **kwargs): calls.append(kwargs)
        actions = ChatActions(Bot(), 'group', lambda: active, clock=lambda: now[0])
        await actions.tick(); await actions.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['action'], 'typing')
        self.assertEqual(calls[0]['message_thread_id'], 7)
        now[0] += 4; await actions.tick()
        self.assertEqual(len(calls), 2)
        active.clear(); now[0] += 4; await actions.tick()
        self.assertEqual(len(calls), 2)
        await actions.close()

    async def test_upload_precedes_typing_refreshes_and_cleans_up_on_failure(self):
        calls, now = [], [10.0]
        class Bot:
            async def send_chat_action(self, **kwargs): calls.append(kwargs['action'])
        actions = ChatActions(Bot(), 'group', lambda: {7}, clock=lambda: now[0])
        await actions.tick()
        with self.assertRaisesRegex(ValueError, 'document failure'):
            async with actions.upload_document(7):
                await actions.tick()
                now[0] += 4; await actions.tick()
                self.assertEqual(calls, ['typing', 'upload_document', 'upload_document'])
                raise ValueError('document failure')
        self.assertFalse(actions.uploads)
        await actions.tick()
        self.assertEqual(calls[-1], 'typing')

    async def test_status_failure_is_nonfatal_and_loop_cancels(self):
        class Bot:
            async def send_chat_action(self, **kwargs): raise RuntimeError('secret URL')
        asleep = asyncio.Event()
        async def sleep(seconds):
            self.assertGreater(seconds, 3)
            self.assertLessEqual(seconds, 4)
            asleep.set(); await asyncio.Event().wait()
        actions = ChatActions(Bot(), 'group', lambda: {7}, sleep=sleep).start()
        await asyncio.wait_for(asleep.wait(), 1)
        await actions.close()
        self.assertEqual(actions.last_error_type, 'RuntimeError')
        self.assertIsNone(actions.task)


class ActivityTests(unittest.TestCase):
    def test_goal_alone_old_results_waiting_finished_or_expired_never_type(self):
        from hermes_state import SessionDB
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'; home.mkdir()
            output = Path(tmp) / 'results'; output.mkdir()
            native = SessionDB(db_path=home / 'state.db')
            native.create_session(session_id='case', source='cli')
            with sqlite3.connect(home / 'state.db') as db:
                db.execute('INSERT INTO state_meta(key,value) VALUES(?,?)', ('goal:case','{"status":"active"}'))
            db.close()
            with sqlite3.connect(output / 'activity.sqlite3') as events:
                events.execute('CREATE TABLE events(kind TEXT,session TEXT,time REAL)')
                events.execute('INSERT INTO events VALUES(?,?,?)', ('tool_start', 'case', 99))
            events.close()
            item = dict(sessions=['case'], output_root=str(output))
            try:
                self.assertFalse(case_is_working(home, item, now=100))
                with sqlite3.connect(home / 'state.db') as db:
                    db.execute('INSERT INTO session_turn_leases VALUES(?,?,?,?)', ('case','synthetic',100,200))
                db.close()
                with patch('hermes_state._compression_lock_holder_process_is_dead', return_value=False):
                    self.assertFalse(case_is_working(home, item, now=101))
                    for kind, expected in [('turn_start',True),('pre_api_request',True),
                            ('tool_start',True),('api_request_error',False),('pre_api_request',True),
                            ('turn_complete',False)]:
                        with sqlite3.connect(output / 'activity.sqlite3') as events:
                            events.execute('INSERT INTO events VALUES(?,?,?)', (kind,'case',101))
                        events.close()
                        self.assertEqual(case_is_working(home,item,now=102), expected, kind)
                    with sqlite3.connect(output / 'activity.sqlite3') as events:
                        events.execute('INSERT INTO events VALUES(?,?,?)', ('tool_start','case',102))
                    events.close()
                    self.assertFalse(case_is_working(home,item,now=201))
                with patch('hermes_state._compression_lock_holder_process_is_dead', return_value=True):
                    self.assertFalse(case_is_working(home,item,now=103))
            finally:
                native.close()

    def test_only_exact_existing_ready_case_topic_and_errors_fail_quietly(self):
        from types import SimpleNamespace
        item = dict(case_id='case',session_id='session')
        router = SimpleNamespace(home='unused',catalog=SimpleNamespace(list=lambda:[item]),
            state={'routes':{'case':dict(status='ready',session='session',thread_id=7)}})
        with patch('telegram_actions.case_is_working',return_value=True):
            self.assertEqual(working_topics(router),{7})
            router.state['routes']['case']['status']='topic_unconfirmed'
            self.assertEqual(working_topics(router),set())
            router.state['routes']['case'].update(status='ready',session='foreign')
            self.assertEqual(working_topics(router),set())


if __name__ == '__main__':
    unittest.main()
