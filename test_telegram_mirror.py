import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from telegram_mirror import Mirror, TopicRouter, group_target, message_text, progress_text


class MirrorTests(unittest.TestCase):
    def test_group_startup_explicit_destination_or_same_profile_saved_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            cfg={'plugins':{'entries':{'forsic':{'settings':{'connections':{'telegram_group':'https://t.me/Case_Group'}}}}}}
            with patch('hermes_cli.config.get_config_path',return_value=home/'config.yaml'), \
                 patch('hermes_cli.config.load_config_readonly',return_value=cfg) as load:
                self.assertEqual(group_target(home), '@case_group')
                self.assertEqual(group_target(home, '-100123456'), -100123456)
                self.assertEqual(group_target(home, '@Other_Group'), '@other_group')
                self.assertEqual(load.call_count,1)
                with self.assertRaisesRegex(ValueError,'다른 프로필'):
                    group_target(home/'other')
                self.assertEqual(group_target(home/'other','-100987654'),-100987654)
            with patch('hermes_cli.config.get_config_path',return_value=home/'config.yaml'), \
                 patch('hermes_cli.config.load_config_readonly',return_value={}):
                with self.assertRaisesRegex(ValueError,'그룹 ID'):
                    group_target(home)

    def test_new_group_cannot_retarget_existing_case_routes_or_cursors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'routing.json'
            state={'chat_id':'-100123456','since':1,'routes':{'case-A':{'thread_id':3,'status':'ready'}}}
            path.write_text(json.dumps(state))
            before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'기존 설정과 다릅니다'):
                TopicRouter(root,root/'intake',path,-100987654,'existing-session',root/'legacy.json')
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse((root/'telegram-cases').exists())

    def test_only_public_dialogue(self):
        row = dict(role='assistant', display_kind=None, _compressed_summary=0, active=1,
                   compacted=0, content='확인했어요', reasoning='private')
        self.assertEqual(message_text(row), '확인했어요')
        for field, value in [('role', 'tool'), ('role', 'system'), ('display_kind', 'hidden'),
                             ('_compressed_summary', 1), ('active', 0), ('compacted', 1)]:
            self.assertEqual(message_text({**row, field: value}), '')
        self.assertNotIn('private', message_text(row))

    def test_progress_not_raw_evidence(self):
        start = dict(kind='tool_start', data=json.dumps(dict(tool='forsic_read', reason='설정 변화를 확인해요', path='config')))
        self.assertIn('설정 변화를 확인해요', progress_text(start))
        result = dict(kind='tool_result', data=json.dumps(dict(tool='forsic_read', stdout='secret raw evidence', lines=[{'text':'raw'}])))
        self.assertNotIn('raw', progress_text(result))
        result['data'] = json.dumps(dict(tool='forsic_read', error='파일을 찾지 못했어요'))
        self.assertIn('파일을 찾지 못했어요', progress_text(result))

    def test_cursor_restart_isolation_and_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                db.executescript('CREATE TABLE sessions(id TEXT, parent_session_id TEXT); '
                    'CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,display_kind TEXT,'
                    '_compressed_summary INTEGER,active INTEGER,compacted INTEGER,timestamp REAL);')
                db.execute('INSERT INTO sessions VALUES (?,NULL)', ('chosen',))
                db.execute('INSERT INTO sessions VALUES (?,NULL)', ('unrelated',))
                db.execute('INSERT INTO sessions VALUES (?,?)', ('compacted', 'chosen'))
                db.execute('INSERT INTO messages VALUES (1,?,?,?,?,0,1,0,?)', ('chosen','user','old history',None,time.time()-10))
            mirror = Mirror(root, root / 'intake', root / 'mirror.json', 'chosen', -123)
            mirror.initialize()
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                for i, sid in enumerate(['chosen','unrelated','compacted'], 2):
                    db.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,0,1,0,?)', (i,sid,'assistant',sid,time.time()))
            sent = []
            async def send(text):
                sent.append(text)
                return len(sent)
            asyncio.run(mirror.tick(send))
            self.assertEqual(sent, ['chosen','compacted'])
            restarted = Mirror(root, root / 'intake', root / 'mirror.json', 'chosen', -123)
            asyncio.run(restarted.tick(send))
            self.assertEqual(len(sent), 2)
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                db.execute('INSERT INTO messages VALUES (5,?,\'assistant\',\'new\',NULL,0,1,0,?)', ('chosen',time.time()))
            async def unknown(text):
                raise TimeoutError('token must never be saved')
            with self.assertRaises(RuntimeError):
                asyncio.run(restarted.tick(unknown))
            self.assertNotIn('token must', (root / 'mirror.json').read_text())
            with self.assertRaises(RuntimeError):
                asyncio.run(restarted.tick(send))
            self.assertEqual(len(sent), 2)


if __name__ == '__main__':
    unittest.main()
