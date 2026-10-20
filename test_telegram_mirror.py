import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from telegram_mirror import Mirror, TopicRouter, assistant_plain_text, group_target, message_text, progress_text


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

    def test_assistant_plain_text_preserves_literals_and_link_destinations(self):
        markdown = ('# 조사 결과\n\n**확인** 및 _검토_ · [E:abc_123]\n\n'
                    '- `/srv/case_one/a_b.log`\n'
                    '- [문서](https://example.test/a_b?q=x_y)\n\n'
                    '```sh\n  grep "**literal**" /srv/case_one/a_b.log\n'
                    '  echo [E:abc_123]\n```')
        self.assertEqual(assistant_plain_text(markdown),
            '조사 결과\n\n확인 및 검토 · [E:abc_123]\n\n'
            '• /srv/case_one/a_b.log\n• 문서 (https://example.test/a_b?q=x_y)\n\n'
            '  grep "**literal**" /srv/case_one/a_b.log\n  echo [E:abc_123]')
        with self.subTest('tables and quoted lists remain readable'):
            self.assertEqual(assistant_plain_text(
                '| 경로 | 결과 |\n|---|---|\n| `/srv/a_b` | **확인** |\n\n'
                '> 근거 [E:abc123]\n\n3. 첫째\n4. 둘째\n'),
                '경로 | 결과\n/srv/a_b | 확인\n\n근거 [E:abc123]\n\n3. 첫째\n4. 둘째')
        with self.subTest('unformatted paths and reference links'):
            literal = r'/srv/case_one/a_b.log C:\case_one\a_b.log [E:abc_123]'
            self.assertEqual(assistant_plain_text(literal), literal)
            self.assertEqual(assistant_plain_text(
                '[원문][source] · ![화면](/local/case_one.png)\n\n'
                '[source]: https://example.test/a_b?q=x_y'),
                '원문 (https://example.test/a_b?q=x_y) · 화면 (/local/case_one.png)')

    def test_outbound_assistant_plaintext_after_redaction_before_chunks_user_unchanged(self):
        from gateway.platforms.base import utf16_len
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                db.executescript('CREATE TABLE sessions(id TEXT, parent_session_id TEXT); '
                    'CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,display_kind TEXT,'
                    '_compressed_summary INTEGER,active INTEGER,compacted INTEGER,timestamp REAL);')
                db.execute('INSERT INTO sessions VALUES (?,NULL)', ('chosen',))
            mirror = Mirror(root, root / 'intake', root / 'mirror.json', 'chosen', -123)
            mirror.initialize()
            user = '**literal** /srv/case_one/a_b.log [E:abc_123]'
            secret = 'sk-' + 'syntheticCredential' * 3
            assistant = '# 결과\n\n**마스킹** ' + secret + '\n\n' + '\n'.join(
                '- **확인** 🔎 /srv/case_one/a_b.log [E:abc_123]' for _ in range(160))
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                for i, (role, content) in enumerate((('user', user), ('assistant', assistant), ('assistant', '---')), 1):
                    db.execute('INSERT INTO messages VALUES (?,?,?, ?,NULL,0,1,0,?)',
                               (i, 'chosen', role, content, time.time()))
            sent, users = [], []
            async def send(text):
                sent.append(text)
                return len(sent)
            async def send_user(text):
                users.append(text)
                return len(users)
            asyncio.run(mirror.tick(send, send_user=send_user))
            self.assertEqual(users, [user])
            self.assertGreater(len(sent), 1)
            self.assertTrue(all(text.strip() and utf16_len(text) <= 4000 for text in sent))
            outbound = '\n'.join(sent)
            self.assertNotIn(secret, outbound)
            self.assertNotIn('**', outbound)
            self.assertNotIn('# 결과', outbound)
            self.assertIn('/srv/case_one/a_b.log [E:abc_123]', outbound)
            self.assertEqual(mirror.state['delivered'], ['message:1', 'message:2', 'message:3'])
            self.assertIsNone(mirror.state['pending'])

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
