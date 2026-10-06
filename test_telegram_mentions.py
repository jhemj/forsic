import asyncio
import contextlib
import hashlib
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telegram_mentions import Mentions, mention_message
from forsic_plugin.telegram_bridge import admit, is_telegram_input


def update(text='@PosicBot 어떤 근거인가요?', **changes):
    msg = dict(message_id=42, date=200, message_thread_id=8,
               chat={'id': -123, 'type': 'supergroup'},
               **{'from': {'id': 9, 'is_bot': False, 'first_name': '조사자'}},
               text=text, entities=[{'type':'mention','offset':0,'length':9}])
    msg.update(changes)
    return {'update_id': 100, 'message': msg}


class MentionTests(unittest.TestCase):
    def test_real_telegram_update_serialization_and_failed_admission_retains_input(self):
        from telegram import Update
        wire=Update.de_json(update(),None).to_dict()
        self.assertIsInstance(wire['message']['date'],int)
        self.assertEqual(mention_message(wire,7,'PosicBot',-123,100)['body'],'어떤 근거인가요?')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            inbox=Mentions(root/'hermes',root/'intake',root/'telegram-mentions.json',-123,7,'PosicBot',
                lambda:{'C':{'status':'ready','session':'case','thread_id':8}})
            inbox.state['since']=100; inbox.receive([wire])
            def unavailable(*args): raise OSError('secret source must not enter receipt')
            inbox.dispatch(unavailable)
            row=next(iter(inbox.state['requests'].values()))
            self.assertEqual(row['status'],'waiting_session')
            self.assertEqual(row['error_type'],'OSError')
            self.assertNotIn('secret source',inbox.path.read_text())
            restarted=Mentions(root/'hermes',root/'intake',inbox.path,-123,7,'PosicBot',lambda:{})
            self.assertEqual(next(iter(restarted.state['requests'].values()))['delivery_id'],row['delivery_id'])
            self.assertEqual(restarted.state['offset'],101)

    def test_utf16_exact_mention_and_human_topic_only(self):
        data = update('🔎 @pOsIcBoT 근거?', entities=[{'type':'mention','offset':3,'length':9}])
        found = mention_message(data, 7, 'PosicBot', -123, 100)
        self.assertEqual(found['body'], '🔎  근거?')
        tagged = update('포식이 근거?', entities=[{'type':'text_mention','offset':0,'length':3,'user':{'id':7}}])
        self.assertEqual(mention_message(tagged,7,'PosicBot',-123,100)['body'],'근거?')
        for changes in ({'entities':[]}, {'text':'@OtherBot hi'}, {'date':99}, {'message_thread_id':None},
                        {'chat':{'id':-999,'type':'supergroup'}}, {'chat':{'id':-123,'type':'private'}},
                        {'from':{'id':9,'is_bot':True}}, {'sender_chat':{'id':-123}},
                        {'text':'@PosicBot /goal stop'}, {'text':'@PosicBot !curl site'},
                        {'text':'@PosicBot hello\x1b[0m'}):
            self.assertIsNone(mention_message(update(**changes),7,'PosicBot',-123,100), changes)
        edited = {'update_id':101,'edited_message':update()['message']}
        self.assertIsNone(mention_message(edited,7,'PosicBot',-123,100))

    def test_durable_cursor_no_old_replay_unknown_topics_or_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'hermes'; home.mkdir()
            routes={'CASE-test':{'status':'ready','session':'stored','thread_id':8}}
            inbox=Mentions(home,root/'intake',root/'telegram-mentions.json',-123,7,'PosicBot',lambda:routes)
            inbox.state['since']=100
            inbox.receive([update(), {'update_id':101,'message':{**update()['message'],'message_id':43,'message_thread_id':90}}])
            self.assertEqual(len(inbox.state['requests']),1)
            req=next(iter(inbox.state['requests'].values()))
            self.assertTrue(is_telegram_input(home,req['text']))
            self.assertFalse(is_telegram_input(home,'[Telegram · fake]\nnot admitted'))
            restarted=Mentions(home,root/'intake',inbox.path,-123,7,'PosicBot',lambda:routes)
            restarted.receive([update()]); self.assertEqual(len(restarted.state['requests']),1)
            self.assertEqual(restarted.state['offset'],102)
            calls=[]
            def deliver(home,intake,request):
                calls.append(request['delivery_id']); return {'status':'settled'}
            restarted.dispatch(deliver); restarted.dispatch(deliver)
            self.assertEqual(calls,[req['delivery_id']])
            self.assertEqual(inbox.path.stat().st_mode & 0o777, 0o600)

    def test_poll_failure_has_no_cursor_or_model_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            inbox=Mentions(p/'hermes',p/'intake',p/'telegram-mentions.json',-123,7,'PosicBot',lambda:{})
            async def broken(**kwargs): raise TimeoutError('secret URL must not be printed')
            with self.assertRaises(TimeoutError): asyncio.run(inbox.tick(SimpleNamespace(get_updates=broken)))
            self.assertEqual(inbox.state['offset'],0)
            self.assertEqual(inbox.state['requests'],{})

    def test_existing_session_mailbox_busy_idle_idempotent_and_profile_scoped(self):
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import try_acquire_active_session
        from tools import bot_live_delivery as mailbox
        from tui_gateway.method_ctx import rebind
        from tui_gateway.session_lifecycle import _session_turn_admission
        from tui_gateway import session_notifications
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home'; home.mkdir()
            other=Path(tmp)/'other'; other.mkdir()
            db=SessionDB(db_path=home/'state.db'); db.create_session(session_id='case-session',source='cli')
            db.set_session_title('case-session','Case, not Bot Chat')
            db2=SessionDB(db_path=other/'state.db'); db2.create_session(session_id='case-session',source='cli')
            lease,refusal=try_acquire_active_session(session_id='case-session',surface='tui',config={},registry_home=home,
                metadata={'live_session_id':'live','bot_live_delivery_consumer':True})
            self.assertIsNone(refusal)
            try:
                self.assertIsNone(mailbox.find_canonical_live_owner(home))
                self.assertIsNone(mailbox.find_canonical_live_owner(other,'case-session'))
                owner=mailbox.find_canonical_live_owner(home,'case-session')
                self.assertEqual(owner['session_id'],'case-session')
                req=dict(delivery_id='a'*64,case_id='CASE-test',session_id='case-session',text='Telegram question',
                         author={'id':'telegram:9','name':'조사자','is_bot':False})
                with patch('forsic_plugin.telegram_bridge.Investigations') as catalog:
                    catalog.return_value.get.return_value={'session_id':'case-session','archived':False}
                    self.assertEqual(admit(home,home/'intake',req)['status'],'queued')
                    self.assertEqual(admit(home,home/'intake',req)['status'],'queued')
                seen=[]
                def submit(rid,sid,session,text,**kwargs):
                    seen.append((sid,text,kwargs['turn_author']))
                    kwargs['terminal_callback']({'status':'settled','text':'answer'})
                    return True
                poll=rebind(session_notifications._poll_bot_live_delivery_once,{
                    '_session_home':lambda s:home,'_session_turn_admission':_session_turn_admission,
                    '_run_prompt_submit':submit,'_notif_release_turn':lambda s:s.update(running=False)})
                session={'history_lock':threading.RLock(),'agent':object(),'session_key':'case-session',
                         'active_session_lease':lease,'running':True}
                self.assertFalse(poll('live',session)); self.assertEqual(seen,[])
                session['running']=False
                self.assertFalse(poll('wrong-live',session))
                self.assertTrue(poll('live',session))
                self.assertEqual(seen,[('live','Telegram question',req['author'])])
                with patch('forsic_plugin.telegram_bridge.Investigations') as catalog:
                    catalog.return_value.get.return_value={'session_id':'case-session','archived':False}
                    self.assertEqual(admit(home,home/'intake',req)['status'],'settled')
                self.assertIsNone(mailbox.claim_pending_delivery(home,owner))
            finally:
                lease.release(); db.close(); db2.close()

    def test_mailbox_input_precedes_automatic_goal_but_not_local_queue(self):
        from tui_gateway.method_ctx import rebind
        from tui_gateway import prompt_turn
        calls=[]
        run=rebind(prompt_turn._run_post_turn_followups,{
            '_drain_queued_prompt':lambda *args:False,
            '_session_profile_runtime_scope':lambda session:contextlib.nullcontext(),
            '_poll_bot_live_delivery_once':lambda sid,session:calls.append(sid) or True})
        run('r','live',{}, {},'automatic goal')
        self.assertEqual(calls,['live'])
        run=rebind(prompt_turn._run_post_turn_followups,{
            '_drain_queued_prompt':lambda *args:True,
            '_poll_bot_live_delivery_once':lambda *args:self.fail('must respect local FIFO')})
        run('r','live',{}, {},'automatic goal')

    def test_explicit_case_mailbox_follows_only_compression_lineage(self):
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import try_acquire_active_session, transfer_active_session
        from tools import bot_live_delivery as mailbox
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            db=SessionDB(db_path=home/'state.db')
            db.create_session(session_id='original',source='cli')
            meta={'live_session_id':'live','bot_live_delivery_consumer':True}
            lease,refusal=try_acquire_active_session(session_id='original',surface='tui',config={},registry_home=home,metadata=meta)
            self.assertIsNone(refusal)
            try:
                owner=mailbox.find_canonical_live_owner(home,'original')
                ticket=mailbox.deliver_to_live_owner(home,owner,'retained',delivery_id='b'*64)
                db.end_session('original','compression')
                db.create_session(session_id='tip',source='cli',parent_session_id='original')
                db.create_session(session_id='unrelated',source='cli')
                self.assertTrue(transfer_active_session(lease,session_id='tip',metadata=meta))
                current=mailbox.find_canonical_live_owner(home,'original')
                self.assertEqual(current['session_id'],'tip')
                self.assertTrue(mailbox.owner_holds_delivery(home,ticket))
                self.assertIsNone(mailbox.find_canonical_live_owner(home,'unrelated'))
                self.assertIsNone(mailbox.claim_pending_delivery(home,{**current,'session_id':'unrelated'}))
                self.assertEqual(mailbox.claim_pending_delivery(home,current)['delivery_id'],ticket['delivery_id'])
            finally:
                lease.release(); db.close()

    def test_queued_mention_rebinds_same_case_after_native_owner_restart(self):
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import try_acquire_active_session
        from forsic_plugin.intake import write_json
        from tools import bot_live_delivery as mailbox
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home'; home.mkdir(); intake=Path(tmp)/'intake'
            manifest=intake/'cases'/'CASE-test'/'manifest.json'
            write_json(manifest,dict(case_id='CASE-test',label='synthetic',output_root=str(Path(tmp)/'results')))
            write_json(intake/(hashlib.sha256(b'original').hexdigest()+'.json'),dict(manifest=str(manifest)))
            db=SessionDB(db_path=home/'state.db'); db.create_session(session_id='original',source='cli')
            lease,_=try_acquire_active_session(session_id='original',surface='tui',config={},registry_home=home,
                metadata={'live_session_id':'before','bot_live_delivery_consumer':True})
            request=dict(delivery_id='c'*64,case_id='CASE-test',session_id='original',text='same question',
                         author={'id':'telegram:9','name':'조사자','is_bot':False})
            try:
                self.assertEqual(admit(home,intake,request)['status'],'queued')
                original=mailbox.read_delivery_result(home,request['delivery_id'])
                lease.release()
                db.end_session('original','compression')
                db.create_session(session_id='tip',source='cli',parent_session_id='original')
                lease,_=try_acquire_active_session(session_id='tip',surface='tui',config={},registry_home=home,
                    metadata={'live_session_id':'after','bot_live_delivery_consumer':True})
                current=mailbox.find_canonical_live_owner(home,'original')
                self.assertEqual(admit(home,intake,{**request,'case_id':'another-case'})['status'],'binding_changed')
                self.assertEqual(mailbox.read_delivery_result(home,request['delivery_id']),original)
                self.assertEqual(admit(home,intake,request)['status'],'queued')
                rebound=mailbox.read_delivery_result(home,request['delivery_id'])
                self.assertEqual(rebound['owner'],current)
                for key in ('delivery_id','message','author','sequence','created_at'):
                    self.assertEqual(rebound[key],original[key])
                self.assertEqual(admit(home,intake,request)['status'],'queued')
                self.assertEqual(mailbox.read_delivery_result(home,request['delivery_id']),rebound)
                with self.assertRaises(ValueError):
                    admit(home,intake,{**request,'author':{'id':'telegram:other'}})
                claimed=mailbox.claim_pending_delivery(home,current)
                self.assertEqual(claimed['delivery_id'],request['delivery_id'])
                lease.release()
                lease,_=try_acquire_active_session(session_id='tip',surface='tui',config={},registry_home=home,
                    metadata={'live_session_id':'third','bot_live_delivery_consumer':True})
                self.assertEqual(admit(home,intake,request)['status'],'claimed')
                self.assertEqual(mailbox.read_delivery_result(home,request['delivery_id']),claimed)
                settled=mailbox.complete_delivery(home,request['delivery_id'],status='settled',reply='answer')
                self.assertEqual(admit(home,intake,request)['status'],'settled')
                self.assertEqual(mailbox.read_delivery_result(home,request['delivery_id']),settled)
            finally:
                lease.release(); db.close()

    def test_first_mention_wakes_only_open_idle_stored_consumer_without_a_turn(self):
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import try_acquire_active_session, active_session_registry_snapshot
        from forsic_plugin.intake import write_json
        from tools import bot_live_delivery as mailbox
        from tui_gateway.method_ctx import rebind
        from tui_gateway.session_lifecycle import _session_turn_admission
        from tui_gateway import session_notifications
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home'; home.mkdir(); other=Path(tmp)/'other'; other.mkdir(); intake=Path(tmp)/'intake'
            manifest=intake/'cases'/'CASE-test'/'manifest.json'
            write_json(manifest,dict(case_id='CASE-test',label='synthetic',output_root=str(Path(tmp)/'results')))
            write_json(intake/(hashlib.sha256(b'original').hexdigest()+'.json'),dict(manifest=str(manifest)))
            db=SessionDB(db_path=home/'state.db'); db.create_session(session_id='original',source='cli')
            db2=SessionDB(db_path=other/'state.db'); db2.create_session(session_id='original',source='cli')
            sessions=[]; submitted=[]
            def claim(key,*,live_session_id,surface,profile_home):
                return try_acquire_active_session(session_id=key,surface=surface,config={},registry_home=profile_home,
                    metadata={'live_session_id':live_session_id,'bot_live_delivery_consumer':True})
            def submit(rid,sid,session,text,**kw):
                submitted.append(text); kw['terminal_callback']({'status':'settled','text':'reply'}); return True
            poll=rebind(session_notifications._poll_bot_live_delivery_once,{
                '_session_home':lambda s:s['profile_home'],'_session_turn_admission':_session_turn_admission,
                '_claim_active_session_slot':claim,'_session_source':lambda s:'tui',
                '_run_prompt_submit':submit,'_notif_release_turn':lambda s:s.update(running=False)})
            def session(key,profile):
                row=dict(session_key=key,profile_home=profile,history_lock=threading.RLock(),agent=object(),running=False)
                sessions.append(row); return row
            target=session('original',home); foreign=session('foreign',home); other_target=session('original',other)
            req=dict(delivery_id='e'*64,case_id='CASE-test',session_id='original',text='first mention',
                     author={'id':'telegram:9','name':'조사자','is_bot':False})
            try:
                self.assertFalse(poll('live',target))
                self.assertEqual(active_session_registry_snapshot(registry_home=home),[])
                self.assertEqual(admit(home,intake,req)['status'],'waiting_session')
                self.assertFalse(poll('foreign',foreign)); self.assertFalse(poll('other',other_target))
                target['running']=True; self.assertFalse(poll('live',target)); target['running']=False
                self.assertEqual(active_session_registry_snapshot(registry_home=home),[])
                self.assertFalse(poll('live',target))  # Advertise ownership, never a synthetic user/model turn.
                self.assertEqual(submitted,[])
                self.assertIsNotNone(mailbox.find_canonical_live_owner(home,'original'))
                self.assertIsNone(mailbox.find_canonical_live_owner(other,'original'))
                self.assertEqual(admit(home,intake,req)['status'],'queued')
                self.assertTrue(poll('live',target)); self.assertEqual(submitted,['first mention'])
                self.assertEqual(admit(home,intake,req)['status'],'settled')
                self.assertFalse(poll('second-window',session('original',home)))
                self.assertEqual(len(active_session_registry_snapshot(registry_home=home)),1)
            finally:
                for s in sessions:
                    if lease:=s.get('active_session_lease'): lease.release()
                db.close(); db2.close()

    def test_queued_rebind_fences_claim_race_and_foreign_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from hermes_state import SessionDB
        from hermes_cli.active_sessions import try_acquire_active_session
        from tools import bot_live_delivery as mailbox
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home'; home.mkdir(); other=Path(tmp)/'other'; other.mkdir()
            db=SessionDB(db_path=home/'state.db')
            for session in ('case','foreign'): db.create_session(session_id=session,source='cli')
            other_db=SessionDB(db_path=other/'state.db'); other_db.create_session(session_id='case',source='cli')
            lease,_=try_acquire_active_session(session_id='case',surface='tui',config={},registry_home=home,
                metadata={'live_session_id':'before','bot_live_delivery_consumer':True})
            foreign_lease,_=try_acquire_active_session(session_id='foreign',surface='tui',config={},registry_home=home,
                metadata={'live_session_id':'foreign','bot_live_delivery_consumer':True})
            other_lease,_=try_acquire_active_session(session_id='case',surface='tui',config={},registry_home=other,
                metadata={'live_session_id':'other-profile','bot_live_delivery_consumer':True})
            try:
                owner=mailbox.find_canonical_live_owner(home,'case')
                row=mailbox.deliver_to_live_owner(home,owner,'keep me',delivery_id='d'*64)
                other_owner=mailbox.find_canonical_live_owner(other,'case')
                other_row=mailbox.deliver_to_live_owner(other,other_owner,'other payload',delivery_id=row['id'])
                for fake in ({**owner,'lease_id':'fake'}, mailbox.find_canonical_live_owner(home,'foreign'), other_owner):
                    with self.assertRaises(ValueError): mailbox.rebind_queued_delivery(home,row['id'],fake)
                self.assertEqual(mailbox.read_delivery_result(home,row['id']),row)
                self.assertEqual(mailbox.read_delivery_result(other,row['id']),other_row)
                lease.release()
                lease,_=try_acquire_active_session(session_id='case',surface='tui',config={},registry_home=home,
                    metadata={'live_session_id':'after','bot_live_delivery_consumer':True})
                current=mailbox.find_canonical_live_owner(home,'case')
                barrier=threading.Barrier(2)
                def claim():
                    barrier.wait(timeout=5); return mailbox.claim_pending_delivery(home,owner)
                def rebind():
                    barrier.wait(timeout=5); return mailbox.rebind_queued_delivery(home,row['id'],current)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first=pool.submit(claim); second=pool.submit(rebind)
                    old_claim=first.result(timeout=10); second.result(timeout=10)
                if old_claim:
                    self.assertEqual(mailbox.read_delivery_result(home,row['id']),old_claim)
                    self.assertIsNone(mailbox.claim_pending_delivery(home,current))
                else:
                    new_claim=mailbox.claim_pending_delivery(home,current)
                    self.assertEqual(new_claim['id'],row['id'])
                    self.assertEqual(new_claim['owner'],current)
                self.assertIsNone(mailbox.claim_pending_delivery(home,owner))
                self.assertIsNone(mailbox.claim_pending_delivery(home,current))
                self.assertEqual(mailbox.read_delivery_result(other,row['id']),other_row)
            finally:
                lease.release(); foreign_lease.release(); other_lease.release(); db.close(); other_db.close()

    def test_imported_native_question_is_not_mirrored_twice(self):
        from hermes_state import SessionDB
        from telegram_mirror import Mirror
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'hermes'; home.mkdir()
            db=SessionDB(db_path=home/'state.db'); db.create_session(session_id='case',source='cli')
            inbox=Mentions(home,root/'intake',root/'telegram-mentions.json',-123,7,'PosicBot',
                lambda:{'C':{'status':'ready','session':'case','thread_id':8}})
            inbox.state['since']=100; inbox.receive([update()])
            text=next(iter(inbox.state['requests'].values()))['text']
            mirror=Mirror(home,root/'intake',root/'mirror.json','case',-123); mirror.initialize()
            db.append_message('case','user',text)
            db.append_message('case','assistant','이번 질문의 답이에요')
            db.append_message('case','user','웹에서 입력한 별도 질문')
            sent=[]; users=[]
            async def send(text): sent.append(text); return len(sent)
            async def send_user(text): users.append(text); return len(users)
            asyncio.run(mirror.tick(send,send_user=send_user))
            self.assertEqual(sent,['이번 질문의 답이에요'])
            self.assertEqual(users,['웹에서 입력한 별도 질문'])
            db.close()
if __name__=='__main__': unittest.main()
