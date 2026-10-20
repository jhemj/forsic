import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from forsic_plugin.intake import write_json
from forsic_plugin.investigations import Investigations
from telegram_mirror import Mirror, TopicRouter


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.intake = self.root / 'intake'
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,title TEXT,started_at REAL,'
                'ended_at REAL,last_activity_at REAL,archived INTEGER); CREATE TABLE state_meta(key TEXT,value TEXT);'
                'CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,display_kind TEXT,'
                '_compressed_summary INTEGER DEFAULT 0,active INTEGER DEFAULT 1,compacted INTEGER DEFAULT 0,timestamp REAL);')
        self.catalog = Investigations(self.root, self.intake)

    def tearDown(self):
        self.tmp.cleanup()

    def case(self, cid, sid, *, parent=None, created=None, synthetic=False, archived=0):
        created = time.time() if created is None else created
        manifest = self.intake / 'cases' / cid / 'case.json'
        if not manifest.exists():
            evidence = self.root / 'evidence' / cid
            evidence.mkdir(parents=True)
            write_json(manifest, dict(case_id=cid,label='same name',question=cid,synthetic=synthetic,
                evidence_root=str(evidence),output_root=str(manifest.parent / 'results')))
        write_json(self.intake / (hashlib.sha256(sid.encode()).hexdigest() + '.json'),
                   dict(manifest=str(manifest),created_at=created,stage='goal_active'))
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,NULL,?,?)', (sid,parent,sid,created,created,archived))
        return manifest

    def message(self, sid, text):
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,'assistant',?,?)", (sid,text,time.time()))

    def router(self, seed='first'):
        return TopicRouter(self.root,self.intake,self.root/'routing.json',-123,seed,self.root/'legacy.json')

    def test_compaction_directory_and_native_archive(self):
        self.case('case-one','first',created=time.time()-20)
        self.case('case-one','child',parent='first')
        self.case('case-two','other',archived=1)
        with closing(sqlite3.connect(self.root/'state.db')) as db, db:
            db.execute('INSERT INTO state_meta VALUES (?,?)', ('goal:child',json.dumps({'status':'done'})))
        before=(self.root/'state.db').read_bytes()
        items=self.catalog.list()
        self.assertEqual(len(items),2)
        one=self.catalog.get(case_id='case-one')
        self.assertEqual(one['session_id'],'first')
        self.assertEqual(one['resume_session_id'],'child')
        self.assertEqual(one['status'],'ended')
        self.assertTrue(self.catalog.get(case_id='case-two')['archived'])
        self.assertEqual(before,(self.root/'state.db').read_bytes())
        self.assertNotIn('manifest',self.catalog.public()[0])

    def test_other_case_child_never_mixed_and_scoped_api(self):
        self.case('case-one','first')
        self.case('case-two','fork',parent='first')
        m=Mirror(self.root,self.intake,self.root/'cursor.json','first',-123,'case-one')
        m.initialize()
        self.message('first','one'); self.message('fork','two')
        self.assertEqual([text for _,text in m.batch()],['one'])
        from forsic_plugin.dashboard import plugin_api as api
        with patch.object(api,'directory',return_value=self.catalog):
            self.assertEqual(api.status(case_id='case-one')['case']['case_id'],'case-one')
            self.assertEqual(api.status(case_id='case-two')['case']['case_id'],'case-two')
            with self.assertRaises(HTTPException): api.status(case_id='../missing')
            with self.assertRaises(HTTPException): api.evidence('not-in-case',case_id='case-two')

    def test_usage_sums_physical_calls_across_compression_only(self):
        self.case('one','root');self.case('one','child',parent='root')
        self.case('one','branch',parent='root');self.case('two','other',parent='root')
        with closing(sqlite3.connect(self.root/'state.db')) as db,db:
            db.executescript('ALTER TABLE sessions ADD COLUMN end_reason TEXT;'
                'ALTER TABLE sessions ADD COLUMN model_config TEXT;'
                'ALTER TABLE sessions ADD COLUMN source TEXT;'
                'CREATE TABLE session_model_usage(session_id TEXT,task TEXT,input_tokens INTEGER,output_tokens INTEGER,'
                'cache_read_tokens INTEGER,cache_write_tokens INTEGER,api_call_count INTEGER);')
            db.execute("UPDATE sessions SET end_reason='compression',ended_at=? WHERE id='root'",(time.time(),))
            db.execute("UPDATE sessions SET model_config=? WHERE id='branch'",(json.dumps({'_branched_from':'root'}),))
            db.executemany('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?)',[
                ('root','',100,20,900,0,2),('child','',30,40,300,10,1),
                ('child','compression',500,50,0,0,1),('branch','',7,3,0,0,1),('other','',8000,2,0,0,1)])
        before=(self.root/'state.db').read_bytes()
        one=self.catalog.get(case_id='one')
        usage=next(c for c in one['conversations'] if c['id']=='root')['usage']
        self.assertEqual(usage['total_tokens'],1950)
        self.assertEqual(usage['prompt_tokens'],1840)
        self.assertEqual(usage['output_tokens'],110)
        self.assertEqual(usage['cache_read_tokens'],1200)
        self.assertEqual(usage['auxiliary_tokens'],550)
        self.assertEqual(usage['api_call_count'],4)
        self.assertEqual(next(c for c in one['conversations'] if c['id']=='branch')['usage']['total_tokens'],10)
        self.assertEqual((self.root/'state.db').read_bytes(),before)

    def test_missing_usage_is_unknown_not_zero(self):
        self.case('one','first')
        self.assertIsNone(self.catalog.get(case_id='one')['conversations'][0]['usage'])

    def goal(self, sid, status):
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.execute('DELETE FROM state_meta WHERE key=?', ('goal:' + sid,))
            db.execute('INSERT INTO state_meta VALUES (?,?)', ('goal:' + sid, json.dumps({'status': status})))

    def test_admission_uses_native_lifecycle_without_heartbeat_expiry(self):
        self.case('one', 'old', created=time.time() - 86400)
        self.goal('old', 'active')
        result = self.catalog.new_investigation()
        self.assertFalse(result['allowed'])
        self.assertEqual(result['active_case'], dict(case_id='one', label='same name',
            resume_session_id='old', status='investigating', goal_id='', closable=False))
        self.goal('old', 'paused')
        paused = self.catalog.new_investigation()
        self.assertFalse(paused['allowed'])
        self.assertEqual(paused['active_case']['status'], 'paused')
        self.assertIn('일시 중지', paused['reason'])
        self.goal('old', 'done')
        self.assertEqual(self.catalog.new_investigation(), dict(allowed=True, reason=None, active_case=None))
        self.goal('old', 'cleared')
        final_before = (self.root / 'state.db').read_bytes()
        self.assertTrue(self.catalog.new_investigation()['allowed'])
        self.assertEqual(final_before, (self.root / 'state.db').read_bytes())

    def test_another_case_active_blocks_despite_newer_completed_case(self):
        self.case('one', 'old', created=time.time() - 86400)
        self.case('two', 'new')
        self.goal('old', 'active'); self.goal('new', 'done')
        self.assertEqual(self.catalog.new_investigation()['active_case']['case_id'], 'one')

    def test_deleted_native_history_does_not_reserve_or_hide_active_case(self):
        self.case('history', 'deleted')
        self.goal('deleted', 'active')
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.execute('DELETE FROM sessions WHERE id=?', ('deleted',))
        # Native deletion preserves Forsic intake/ledger and may retain goal meta.
        self.assertTrue(self.catalog.new_investigation()['allowed'])
        self.case('current', 'current')
        self.goal('current', 'active')
        result = self.catalog.new_investigation()
        self.assertFalse(result['allowed'])
        self.assertEqual(result['active_case']['case_id'], 'current')
        self.goal('current', 'done')
        self.assertTrue(self.catalog.new_investigation()['allowed'])

    def test_corrupt_or_unknown_state_fails_closed_and_api_explains(self):
        from forsic_plugin.dashboard import plugin_api as api
        manifest = self.case('one', 'first')
        for raw in ('broken json', json.dumps({'status': 'future-status'}), '[]'):
            with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
                db.execute('DELETE FROM state_meta')
                db.execute('INSERT INTO state_meta VALUES (?,?)', ('goal:first', raw))
            result = self.catalog.new_investigation()
            self.assertFalse(result['allowed'])
            self.assertIn('확인', result['reason'])
        self.goal('first', 'done')
        manifest.write_text('broken manifest')
        with patch.object(api, 'directory', return_value=self.catalog):
            response = api.investigations()
        self.assertEqual(response['cases'], [])
        self.assertFalse(response['new_investigation']['allowed'])
        self.assertIn('읽을 수 없어', response['new_investigation']['reason'])

    def test_case_conversations_scope_and_compression_boundaries(self):
        now = time.time()
        first = self.case('case-one', 'first', created=now - 40)
        self.case('case-one', 'compressed', parent='first', created=now - 30)
        self.case('case-one', 'branch', parent='first', created=now - 20)
        self.case('case-two', 'other', parent='first', created=now - 10)
        with closing(sqlite3.connect(self.root / 'state.db')) as db, db:
            db.execute('ALTER TABLE sessions ADD COLUMN end_reason TEXT')
            db.execute('ALTER TABLE sessions ADD COLUMN model_config TEXT')
            db.execute('ALTER TABLE sessions ADD COLUMN source TEXT')
            db.execute("UPDATE sessions SET end_reason='compression',ended_at=? WHERE id='first'", (now - 30,))
            db.execute("UPDATE sessions SET model_config=? WHERE id='branch'", (json.dumps({'_branched_from':'first'}),))
        case = self.catalog.get(case_id='case-one')
        self.assertEqual({tuple(c['session_ids']) for c in case['conversations']}, {('first', 'compressed'), ('branch',)})
        continuation = next(c for c in case['conversations'] if c['id'] == 'first')
        self.assertEqual(continuation['resume_session_id'], 'compressed')
        self.assertIsNone(self.catalog.get(case_id='case-one', session_id='other'))
        self.assertEqual(self.catalog.get(case_id='case-one', session_id='compressed')['case_id'], 'case-one')
        from forsic_plugin.dashboard import plugin_api as api
        with patch.object(api, 'directory', return_value=self.catalog):
            with self.assertRaises(HTTPException) as error:
                api.selected_case(case_id='case-one', session_id='other')
            self.assertEqual(error.exception.status_code, 404)
            response = api.investigation_conversations('case-one')
            self.assertEqual(response['conversations'], case['conversations'])
        public = next(c for c in self.catalog.public() if c['case_id'] == 'case-one')
        self.assertEqual(public['scope'], json.loads(first.read_text())['evidence_root'])
        self.assertEqual(set(public['session_ids']), {'first', 'compressed', 'branch'})
        self.assertNotIn('manifest', public)
        self.assertNotIn('output_root', public)

    def test_topics_migration_new_case_and_restart(self):
        self.case('case-one','first',created=time.time()-100)
        old=Mirror(self.root,self.intake,self.root/'legacy.json','first',-123)
        old.initialize(); self.message('first','already sent')
        async def sent(_): return 9
        asyncio.run(old.tick(sent))
        self.case('old-case','historic',created=time.time()-50)
        router=self.router()
        self.case('case-two','second')
        self.case('test-case','test',synthetic=True)
        self.message('first','first new');self.message('second','second new');self.message('test','not sent')
        topics=[]; messages=[]
        async def create(name): topics.append(name);return len(topics)+20
        async def send(thread,text): messages.append((thread,text));return len(messages)+30
        asyncio.run(router.tick(create,send))
        self.assertEqual(len(topics),2)
        self.assertEqual(len({topic for topic,_ in messages}),2)
        self.assertEqual({text for _,text in messages},{'first new','second new'})
        self.assertEqual(len(router.state['routes']),2)
        self.case('case-one','compacted',parent='first')
        self.message('compacted','continued')
        resumed=self.router()
        asyncio.run(resumed.tick(create,send))
        self.assertEqual(len(topics),2)
        self.assertEqual(messages[-1],(resumed.state['routes']['case-one']['thread_id'],'continued'))
        asyncio.run(self.router().tick(create,send))
        self.assertEqual(len(messages),3)

    def test_unknown_topic_and_delivery_do_not_duplicate(self):
        self.case('one','first')
        router=self.router()
        count=[]
        async def create(_): count.append(1);raise TimeoutError('private token')
        async def send(*_): self.fail('not ready')
        with self.assertRaises(RuntimeError): asyncio.run(router.tick(create,send))
        asyncio.run(self.router().tick(create,send))
        self.assertEqual(count,[1])
        self.assertNotIn('private token',(self.root/'routing.json').read_text())

    def test_two_bots_same_topic_no_prefix_or_replay(self):
        self.case('one','first'); router=self.router()
        self.message('first','📌 확인한 내용')
        with closing(sqlite3.connect(self.root/'state.db')) as db, db:
            db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,'user',?,?)",
                       ('first','어떤 근거가 있어?',time.time()))
        assistant=[]; users=[]; topics=[]
        async def create(name): topics.append(name); return 32
        async def send(thread,text): assistant.append((thread,text)); return 40
        async def send_user(thread,text): users.append((thread,text)); return 41
        # A missing user-bot callback does not fall back to the assistant bot.
        asyncio.run(router.tick(create,send))
        self.assertEqual(assistant,[(32,'📌 확인한 내용')])
        asyncio.run(self.router().tick(create,send,send_user=send_user))
        asyncio.run(self.router().tick(create,send,send_user=send_user))
        self.assertEqual(users,[(32,'어떤 근거가 있어?')])
        self.assertEqual(len(assistant),1)
        self.assertEqual(len(topics),1)

    def test_unknown_user_send_keeps_bot_identity_and_no_retry(self):
        self.case('one','first'); router=self.router()
        with closing(sqlite3.connect(self.root/'state.db')) as db, db:
            db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,'user',?,?)",
                       ('first','질문',time.time()))
        calls=[]
        async def create(_): return 32
        async def assistant(*_): self.fail('User messages must not use assistant bot')
        async def user(*_): calls.append(1); raise TimeoutError('secret-not-for-log')
        asyncio.run(router.tick(create,assistant,send_user=user))
        asyncio.run(self.router().tick(create,assistant,send_user=user))
        self.assertEqual(calls,[1])
        cursor=next((self.root/'telegram-cases').glob('*.json')).read_text()
        self.assertEqual(json.loads(cursor)['pending']['sender'],'user_bot')
        self.assertNotIn('secret-not-for-log',cursor)

    def test_progress_coalesced_and_card_reused_after_restart(self):
        from forsic_plugin.evidence import Case
        case=Case(self.case('one','first'))
        mirror=Mirror(self.root,self.intake,self.root/'cursor.json','first',-123,'one')
        mirror.initialize()
        case.record('tool_start',{'tool':'forsic_read','reason':'첫 작업'},'first')
        case.record('tool_result',{'tool':'forsic_read'},'first')
        case.record('tool_start',{'tool':'forsic_search','reason':'최신 작업'},'first')
        self.message('first','결론은 따로 남김')
        edits=[];messages=[]
        async def update(mid,text): edits.append((mid,text));return 45
        async def send(text): messages.append(text);return 46
        asyncio.run(mirror.tick(send,update))
        self.assertEqual(len(edits),1)
        self.assertIn('최신 작업',edits[0][1])
        self.assertEqual(messages,['결론은 따로 남김'])
        case.record('tool_result',{'tool':'forsic_search'},'first')
        restarted=Mirror(self.root,self.intake,self.root/'cursor.json','first',-123,'one')
        asyncio.run(restarted.tick(send,update))
        self.assertEqual(edits[-1][0],45)
        case.record('tool_result',{'tool':'forsic_search'},'first')
        asyncio.run(restarted.tick(send,update))
        self.assertEqual(len(edits),2)
        self.assertEqual(len(restarted.state['delivered']),6)

    def test_progress_connected_card_only_once(self):
        self.case('one','first')
        mirror=Mirror(self.root,self.intake,self.root/'cursor.json','first',-123,'one')
        mirror.initialize();edits=[]
        async def update(mid,text): edits.append((mid,text));return 77
        async def send(_): self.fail('Connection status is not an alert')
        asyncio.run(mirror.tick(send,update))
        asyncio.run(mirror.tick(send,update))
        self.assertEqual(len(edits),1)
        self.assertIn('연결 완료',edits[0][1])
        self.assertEqual(mirror.state['progress_message_id'],77)

    def test_unknown_message_does_not_stop_other_cases(self):
        self.case('one','first');router=self.router();self.case('two','second')
        self.message('first','lost');self.message('second','delivered')
        async def create(name): return len(name)
        calls=[]
        async def send(thread,text):
            calls.append(text)
            if 'lost' in text: raise TimeoutError()
            return 1
        asyncio.run(router.tick(create,send));asyncio.run(self.router().tick(create,send))
        self.assertEqual(sorted(calls),['delivered','lost'])


class NativeConversationCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'hermes'
        self.home.mkdir()
        self.intake = self.root / 'intake'
        self.catalog = Investigations(self.home, self.intake)
        (self.root / 'evidence').mkdir()
        self.evidence = self.root / 'evidence' / 'synthetic.txt'
        self.evidence.write_text('Synthetic fixture only. No investigation conclusions.\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_intake_and_empty_case_conversation_are_idempotent(self):
        from hermes_state import SessionDB
        from forsic_plugin.intake import Intake
        from forsic_plugin.investigations import RequestConflict
        from forsic_plugin.dashboard import plugin_api as api
        original = self.evidence.read_bytes()
        request = api.InvestigationInput(path=str(self.evidence), request_id=uuid.uuid4())
        with patch.object(api, 'directory', return_value=self.catalog):
            created = api.investigation_create(request)
            self.assertEqual(api.investigation_create(request), created)
            other_path = api.InvestigationInput(path=str(self.evidence.parent), request_id=request.request_id)
            with self.assertRaises(HTTPException) as conflict:
                api.investigation_create(other_path)
            self.assertEqual(conflict.exception.status_code, 409)
            pointer_before = (self.intake / 'current.json').read_bytes()
            manifest_path = Path(Intake(self.intake).state(created['session_id'])['manifest'])
            manifest_before = manifest_path.read_bytes()
            new_request = api.ConversationInput(request_id=uuid.uuid4())
            fresh = api.investigation_conversation_create(created['case_id'], new_request)
            self.assertEqual(api.investigation_conversation_create(created['case_id'], new_request), fresh)
        self.assertEqual(fresh['case_id'], created['case_id'])
        self.assertNotEqual(fresh['session_id'], created['session_id'])
        self.assertEqual((self.intake / 'current.json').read_bytes(), pointer_before)
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(self.evidence.read_bytes(), original)
        self.assertEqual(Intake(self.intake).state(fresh['session_id'])['stage'], 'case_bound')
        db = SessionDB(self.home / 'state.db', read_only=True)
        try:
            for sid in (created['session_id'], fresh['session_id']):
                self.assertIsNone(db.get_session(sid)['parent_session_id'])
                self.assertEqual(db.resolve_resume_session_id(sid), sid)
                self.assertEqual(db.get_messages(sid), [])
        finally:
            db.close()
        with closing(sqlite3.connect(self.home / 'state.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM state_meta WHERE key LIKE 'goal:%'").fetchone()[0], 0)
        case = self.catalog.public()[0]
        self.assertEqual(case['scope'], str(self.evidence))
        self.assertEqual(len(case['conversations']), 2)
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)
        with self.assertRaises(RequestConflict):
            self.catalog.create_conversation(request.request_id, case_id=created['case_id'])

    def test_evidence_containing_runtime_is_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            self.catalog.create_conversation(uuid.uuid4(), path=str(self.root))
        self.assertFalse(self.intake.exists())
        self.assertFalse((self.home / 'state.db').exists())

    def test_retry_recovers_bound_intake_after_native_session_write_failure(self):
        from hermes_state import SessionDB
        from forsic_plugin.intake import Intake
        request = uuid.uuid4()
        with patch.object(SessionDB, 'create_session', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):
                self.catalog.create_conversation(request, path=str(self.evidence))
        receipt = json.loads((self.intake / 'ui-requests' / (str(request) + '.json')).read_text())
        sid = receipt['session_id']
        manifest = Path(Intake(self.intake).state(sid)['manifest'])
        before = manifest.read_bytes()
        result = self.catalog.create_conversation(request, path=str(self.evidence))
        self.assertEqual(result['session_id'], sid)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(Intake(self.intake).state(sid)['stage'], 'awaiting_question')
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)

    def test_creation_reserves_intake_until_native_completion_and_replays(self):
        from forsic_plugin.dashboard import plugin_api as api
        request = api.InvestigationInput(path=str(self.evidence), request_id=uuid.uuid4())
        with patch.object(api, 'directory', return_value=self.catalog):
            first = api.investigation_create(request)
            availability = api.investigations()['new_investigation']
            self.assertFalse(availability['allowed'])
            self.assertEqual(availability['active_case']['status'], 'waiting')
            self.assertEqual(api.investigation_create(request), first)
            with self.assertRaises(HTTPException) as denied:
                api.investigation_create(api.InvestigationInput(path=str(self.evidence), request_id=uuid.uuid4()))
            self.assertEqual(denied.exception.status_code, 409)
            self.assertEqual(denied.exception.detail, availability['reason'])
            # Opening an empty existing-case conversation remains available.
            fresh = api.investigation_conversation_create(first['case_id'], api.ConversationInput(request_id=uuid.uuid4()))
        with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
            db.execute('INSERT INTO state_meta(key,value) VALUES (?,?)',
                       ('goal:' + first['session_id'], json.dumps({'status': 'done'})))
        self.assertTrue(self.catalog.new_investigation()['allowed'])
        second = self.catalog.create_conversation(uuid.uuid4(), path=str(self.evidence))
        self.assertNotEqual(first['case_id'], second['case_id'])
        self.assertEqual(fresh['case_id'], first['case_id'])

    def test_concurrent_different_requests_create_only_one_case(self):
        from forsic_plugin.investigations import InvestigationUnavailable
        start = threading.Barrier(2)
        def create(_):
            start.wait(timeout=10)
            try:
                return self.catalog.create_conversation(uuid.uuid4(), path=str(self.evidence))
            except InvestigationUnavailable:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)
        with closing(sqlite3.connect(self.home / 'state.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)

    def test_concurrent_same_request_replays_one_receipt(self):
        request = uuid.uuid4()
        start = threading.Barrier(2)
        def create(_):
            start.wait(timeout=10)
            return self.catalog.create_conversation(request, path=str(self.evidence))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)
        with closing(sqlite3.connect(self.home / 'state.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)

    def test_incomplete_receipt_blocks_different_request_but_allows_recovery(self):
        from hermes_state import SessionDB
        from forsic_plugin.investigations import InvestigationUnavailable
        request = uuid.uuid4()
        with patch.object(SessionDB, 'create_session', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.catalog.create_conversation(request, path=str(self.evidence))
        self.assertFalse(self.catalog.new_investigation()['allowed'])
        with self.assertRaises(InvestigationUnavailable):
            self.catalog.create_conversation(uuid.uuid4(), path=str(self.evidence))
        result = self.catalog.create_conversation(request, path=str(self.evidence))
        self.assertEqual(len(list((self.intake / 'cases').iterdir())), 1)
        self.assertEqual(self.catalog.create_conversation(request, path=str(self.evidence)), result)

    def test_completed_manual_turn_releases_but_new_turn_and_intake_reserve(self):
        from forsic_plugin.intake import Intake
        from forsic_plugin.evidence import Case
        request = uuid.uuid4()
        result = self.catalog.create_conversation(request, path=str(self.evidence))
        sid = result['session_id']
        intake = Intake(self.intake)
        state = intake.state(sid)
        case = Case(state['manifest'])
        for stage in ('focused_question', 'goal_manual'):
            with self.subTest(stage=stage):
                state['stage'] = stage
                write_json(intake.path(sid), state)
                case.record('turn_start', {}, sid)
                self.assertFalse(self.catalog.new_investigation()['allowed'])
                case.record('turn_complete', {}, sid)
                self.assertTrue(self.catalog.new_investigation()['allowed'])
                with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
                    db.execute('INSERT INTO session_turn_leases VALUES (?,?,?,?)', (sid, 'synthetic-holder', time.time(), time.time()+300))
                running = self.catalog.new_investigation()
                self.assertFalse(running['allowed'])
                self.assertEqual(running['active_case']['status'], 'investigating')
                with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
                    db.execute('DELETE FROM session_turn_leases')
                self.assertTrue(self.catalog.new_investigation()['allowed'])
        # A completed greeting does not close newly registered intake.
        state['stage'] = 'awaiting_question'
        write_json(intake.path(sid), state)
        self.assertFalse(self.catalog.new_investigation()['allowed'])
        self.assertEqual(self.catalog.create_conversation(request, path=str(self.evidence)), result)

    def test_deleted_completed_receipt_is_history_but_missing_store_is_unknown(self):
        from hermes_state import SessionDB
        first = self.catalog.create_conversation(uuid.uuid4(), path=str(self.evidence))
        db = SessionDB(self.home / 'state.db')
        try:
            db.delete_session(first['session_id'])
        finally:
            db.close()
        self.assertTrue(self.catalog.new_investigation()['allowed'])
        with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
            db.execute('INSERT INTO session_turn_leases VALUES (?,?,?,?)',
                       (first['session_id'], 'synthetic-holder', time.time(), time.time()+300))
        self.assertFalse(self.catalog.new_investigation()['allowed'])
        with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
            db.execute('DELETE FROM session_turn_leases')
        self.assertTrue(self.catalog.new_investigation()['allowed'])
        for path in self.home.glob('state.db*'):
            path.unlink()
        self.assertFalse(self.catalog.new_investigation()['allowed'])


if __name__ == '__main__': unittest.main()
