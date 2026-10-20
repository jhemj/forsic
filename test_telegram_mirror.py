import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from telegram_mirror import Mirror, TopicRouter, assistant_plain_text, group_target, message_text, progress_text


class MirrorTests(unittest.TestCase):
    def setUp(self):
        isolated = tempfile.TemporaryDirectory(); self.addCleanup(isolated.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'HERMES_HOME': isolated.name}).start()
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        token = set_hermes_home_override(isolated.name)
        self.addCleanup(reset_hermes_home_override, token)

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
            with patch.object(mirror, 'batch', return_value=[('clarify:example', assistant)]):
                asyncio.run(mirror.tick(send, send_user=send_user))
            self.assertEqual(users, [])
            self.assertGreater(len(sent), 1)
            self.assertTrue(all(text.strip() and utf16_len(text) <= 4000 for text in sent))
            outbound = '\n'.join(sent)
            self.assertNotIn(secret, outbound)
            self.assertNotIn('**', outbound)
            self.assertNotIn('# 결과', outbound)
            self.assertIn('/srv/case_one/a_b.log [E:abc_123]', outbound)
            self.assertEqual(mirror.state['delivered'], ['clarify:example'])
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
            self.assertEqual(sent, [])
            restarted = Mirror(root, root / 'intake', root / 'mirror.json', 'chosen', -123)
            asyncio.run(restarted.tick(send))
            self.assertEqual(len(sent), 0)
            with closing(sqlite3.connect(root / 'state.db')) as db, db:
                db.execute('INSERT INTO messages VALUES (5,?,\'assistant\',\'new\',NULL,0,1,0,?)', ('chosen',time.time()))
            async def unknown(text):
                raise TimeoutError('token must never be saved')
            with patch.object(restarted, 'batch', return_value=[('clarify:new', 'new user input request')]):
                with self.assertRaises(RuntimeError):
                    asyncio.run(restarted.tick(unknown))
            self.assertNotIn('token must', (root / 'mirror.json').read_text())
            with self.assertRaises(RuntimeError):
                asyncio.run(restarted.tick(send))
            self.assertEqual(len(sent), 0)




class ImportantNotificationTests(unittest.TestCase):
    # Keep the original rendering and connection tests on MirrorTests only.
    def setUp(self):
        MirrorTests.setUp(self)
        isolated = tempfile.TemporaryDirectory(); self.addCleanup(isolated.cleanup)
        self.root = Path(isolated.name); self.home = self.root/'home'; self.home.mkdir()
        self.intake = self.root/'intake'; self.intake.mkdir()
        self.output = self.root/'output'; evidence = self.root/'evidence'; evidence.mkdir()
        manifest = self.root/'case.json'
        manifest.write_text(json.dumps({'case_id':'synthetic-case','evidence_root':str(evidence),'output_root':str(self.output)}))
        from forsic_plugin.evidence import Case
        self.case = Case(manifest)
        with closing(sqlite3.connect(self.home/'state.db')) as db, db:
            db.executescript('CREATE TABLE sessions(id TEXT,parent_session_id TEXT); '
                'CREATE TABLE state_meta(key TEXT PRIMARY KEY,value TEXT); '
                'CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,display_kind TEXT,'
                '_compressed_summary INTEGER,active INTEGER,compacted INTEGER,timestamp REAL);')
            db.executemany('INSERT INTO sessions VALUES (?,?)', [('chosen',None),('compressed','chosen'),('unrelated',None)])
        for session in ('chosen','compressed'):
            (self.intake/(__import__('hashlib').sha256(session.encode()).hexdigest()+'.json')).write_text(json.dumps({'manifest':str(manifest)}))
        self.mirror = Mirror(self.home,self.intake,self.root/'mirror.json','chosen',-123)
        self.mirror.initialize(); self.sent = []

    async def send(self, text):
        self.sent.append(text); return len(self.sent)

    def tick(self):
        asyncio.run(self.mirror.tick(self.send))

    def clarify(self, phase='requested', identity='native-call', **kwargs):
        from telegram_mirror import record_clarification
        record_clarification(self.case, phase, 'clarify', args={'questions':[{'question':'어떤 범위를 선택하시겠어요?','choices':['선택된 범위','확장된 범위']}]},
                             tool_call_id=identity, session_id='chosen', turn_id='native-turn', api_request_id='native-api', **kwargs)

    def goal(self, status, *, session='chosen', created_at=100):
        goal = {'created_at':created_at,'last_turn_at':time.time(),'status':status,'last_reason':'PRIVATE PROVIDER BODY'}
        with closing(sqlite3.connect(self.home/'state.db')) as db, db:
            db.execute('INSERT OR REPLACE INTO state_meta VALUES (?,?)',('goal:'+session,json.dumps(goal)))

    def test_native_user_request_only_not_internal_notes_progress_or_plain_questions(self):
        with closing(sqlite3.connect(self.home/'state.db')) as db, db:
            db.execute('INSERT INTO messages VALUES (1,?,?,?,?,0,1,0,?)',('chosen','assistant','어디를 검사할까요? 도구 실행 중입니다.',None,time.time()))
        self.case.record('tool_start',{'tool':'forsic_read','arguments':{}},'chosen')
        self.case.record('tool_result',{'tool':'forsic_read','error':'ordinary per-tool failure'},'chosen')
        self.case.record('note',{'note_id':'internal','question':'침입했는가?','answer':'미확인','status':'needs_input'},'')
        updates=[]
        async def progress(*args):updates.append(args)
        asyncio.run(self.mirror.tick(self.send,update_progress=progress))
        self.assertEqual(self.sent,[]);self.assertEqual(updates,[])
        self.clarify();self.tick()
        self.assertEqual(len(self.sent),1);self.assertIn('답변이 필요합니다',self.sent[0])
        self.assertIn('어떤 범위를 선택하시겠어요?',self.sent[0]);self.assertNotIn('침입했는가',self.sent[0])
        self.tick();self.assertEqual(len(self.sent),1)

    def test_answered_cancelled_and_foreign_requests_are_not_sent(self):
        self.clarify();self.clarify('resolved');self.tick();self.assertEqual(self.sent,[])
        from telegram_mirror import record_clarification
        record_clarification(self.case,'requested','clarify',args={'questions':[{'question':'foreign'}]},
                             session_id='unrelated',tool_call_id='foreign')
        record_clarification(self.case,'requested','forsic_question',args={'questions':[{'question':'internal'}]},
                             session_id='chosen',tool_call_id='internal')
        record_clarification(self.case,'requested','clarify',args={'questions':[]},session_id='chosen',tool_call_id='invalid')
        self.tick();self.assertEqual(self.sent,[])

    def test_repeated_pause_transitions_survive_restart_without_duplicate_notifications(self):
        self.goal('active'); self.tick()
        self.goal('paused'); self.tick()
        self.assertEqual(len(self.sent), 1)
        self.mirror = Mirror(self.home,self.intake,self.root/'mirror.json','chosen',-123)
        self.tick(); self.assertEqual(len(self.sent), 1)
        self.goal('active'); self.tick()
        self.goal('paused'); self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(set(self.mirror.state['delivered'])), 2)
        self.tick(); self.assertEqual(len(self.sent), 2)

    def test_adopted_material_answer_change_only(self):
        note={'note_id':'material','answer':'Initial bounded assessment','correction_reason':'Independent records changed the explanation',
              'investigation':{'priority':'material','claim_refs':[{'id':'claim-a'}],'assessment':'supported'}}
        self.case.record('note',note)
        self.tick();self.assertEqual(len(self.sent),1)
        self.case.record('note',{**note,'correction_reason':'Formatting change'})
        self.tick();self.assertEqual(len(self.sent),1)
        revised={**note,'answer':'Revised bounded assessment','investigation':{**note['investigation'],'claim_refs':[{'id':'claim-b'}]}}
        self.case.record('note',revised);self.tick();self.assertEqual(len(self.sent),2)
        self.assertIn('중요한 판단 변경',self.sent[-1]);self.assertNotIn('답변이 필요합니다',self.sent[-1])
        self.case.record('note',{**revised,'answer':'Routine context','investigation':{**note['investigation'],'priority':'contextual'}})
        self.tick();self.assertEqual(len(self.sent),2)

    def test_goal_transitions_completion_pause_scope_and_restart(self):
        self.goal('active');self.tick();self.assertEqual(self.sent,[])
        self.goal('done',session='unrelated');self.tick();self.assertEqual(self.sent,[])
        self.goal('done');self.tick();self.assertEqual(len(self.sent),1);self.assertIn('조사 목표 완료',self.sent[-1])
        self.mirror=Mirror(self.home,self.intake,self.mirror.path,'chosen',-123);self.tick();self.assertEqual(len(self.sent),1)
        self.goal('active');self.tick();self.goal('paused');self.tick();self.assertEqual(len(self.sent),2)
        self.assertIn('중단',self.sent[-1]);self.assertNotIn('PRIVATE PROVIDER BODY','\n'.join(self.sent))

    def test_policy_cutover_keeps_old_unknown_attempt_without_backlog_and_new_channel_works(self):
        old={'session':'chosen','chat_id':'-123','since':0,'delivered':['message:5'],
             'pending':{'key':'message:6','chunk':0,'began':100,'channel':'message'},'status':'delivery_unconfirmed','error_type':'TimedOut'}
        self.mirror.path.write_text(json.dumps(old));self.goal('done')
        self.clarify(identity='old-question')
        self.mirror=Mirror(self.home,self.intake,self.mirror.path,'chosen',-123);self.tick();self.assertEqual(self.sent,[])
        self.clarify(identity='new-question');self.tick();self.assertEqual(len(self.sent),1)
        retained=json.loads(self.mirror.path.read_text())
        for field in ('since','pending','status','error_type'):self.assertEqual(retained[field],old[field])
        self.assertIn('message:5',retained['delivered']);self.assertEqual(retained['important_delivery']['status'],'ready')

    def test_uncertain_important_delivery_never_retries_and_logs_no_exception_body(self):
        self.clarify()
        async def uncertain(text):raise TimeoutError('PRIVATE TOKEN URL')
        with self.assertRaises(RuntimeError):asyncio.run(self.mirror.tick(uncertain))
        retained=self.mirror.path.read_text();self.assertNotIn('PRIVATE TOKEN URL',retained)
        self.mirror=Mirror(self.home,self.intake,self.mirror.path,'chosen',-123)
        with self.assertRaises(RuntimeError):self.tick()
        self.assertEqual(self.sent,[])

    def test_same_case_member_can_survive_representative_change(self):
        self.mirror.case_id='synthetic-case'
        with patch('telegram_mirror.Investigations') as catalog:
            catalog.return_value.get.return_value={'session_id':'compressed','sessions':['chosen','compressed']}
            self.assertEqual(self.mirror.sessions(),['chosen','compressed'])
            catalog.return_value.get.return_value={'session_id':'foreign','sessions':['foreign']}
            with self.assertRaises(ValueError):self.mirror.sessions()

    def test_stale_route_does_not_stop_current_case_or_reset_unknown_cursor(self):
        import hashlib
        path=self.root/'routing.json';old={'session':'old-session','chat_id':'-123','since':0,
            'delivered':['message:old'],'pending':{'key':'message:unknown'},'status':'delivery_unconfirmed','error_type':'TimedOut'}
        path.write_text(json.dumps({'chat_id':'-123','since':0,'routes':{
            'stale-case':{'session':'old-session','thread_id':1,'status':'ready'},
            'synthetic-case':{'session':'chosen','thread_id':2,'status':'ready'}}}))
        cursor=self.root/'telegram-cases'/(hashlib.sha256(b'stale-case').hexdigest()+'.json')
        cursor.parent.mkdir();cursor.write_text(json.dumps(old));original=cursor.read_bytes()
        items=[{'case_id':'stale-case','session_id':'replacement','sessions':['replacement'],'created_at':1,'synthetic':False},
               {'case_id':'synthetic-case','session_id':'chosen','sessions':['chosen'],'created_at':1,'synthetic':False}]
        with patch('telegram_mirror.Investigations') as catalog:
            catalog.return_value.list.return_value=items
            catalog.return_value.get.side_effect=lambda case_id:next(item for item in items if item['case_id']==case_id)
            router=TopicRouter(self.home,self.intake,path,-123,'chosen',self.root/'legacy.json')
            create=AsyncMock();sent=[]
            async def send(topic,text):sent.append((topic,text));return len(sent)
            asyncio.run(router.tick(create,send))
            self.clarify();asyncio.run(router.tick(create,send))
            self.assertEqual(router.state['routes']['stale-case']['notification_delivery_status'],'binding_unavailable')
            self.assertEqual(len(sent),1);self.assertEqual(sent[0][0],2)
            self.assertEqual(cursor.read_bytes(),original);create.assert_not_awaited()


    def test_stale_legacy_cursor_migration_does_not_stop_healthy_route(self):
        import hashlib
        path=self.root/'routing.json';legacy=self.root/'legacy.json'
        old={'session':'old-session','chat_id':'-123','since':0,
            'delivered':['message:old'],'pending':{'key':'message:unknown'},
            'status':'delivery_unconfirmed','error_type':'TimedOut'}
        legacy.write_text(json.dumps(old));original=legacy.read_bytes()
        path.write_text(json.dumps({'chat_id':'-123','since':0,'routes':{
            'legacy-case':{'session':'old-session','thread_id':1,'status':'ready'},
            'synthetic-case':{'session':'chosen','thread_id':2,'status':'ready'}}}))
        cursor=self.root/'telegram-cases'/(hashlib.sha256(b'legacy-case').hexdigest()+'.json')
        items=[{'case_id':'legacy-case','session_id':'replacement','sessions':['old-session','replacement'],'created_at':1,'synthetic':False},
               {'case_id':'synthetic-case','session_id':'chosen','sessions':['chosen'],'created_at':1,'synthetic':False}]
        with patch('telegram_mirror.Investigations') as catalog:
            catalog.return_value.list.return_value=items
            catalog.return_value.get.side_effect=lambda case_id:next(item for item in items if item['case_id']==case_id)
            router=TopicRouter(self.home,self.intake,path,-123,'old-session',legacy)
            create=AsyncMock();sent=[]
            async def send(topic,text):sent.append((topic,text));return len(sent)
            asyncio.run(router.tick(create,send))
            self.clarify();asyncio.run(router.tick(create,send))
            self.assertEqual(router.state['routes']['legacy-case']['notification_delivery_status'],'binding_unavailable')
            self.assertEqual(len(sent),1);self.assertEqual(sent[0][0],2)
            self.assertEqual(legacy.read_bytes(),original)
            self.assertFalse(cursor.exists());create.assert_not_awaited()


if __name__ == '__main__': unittest.main()
