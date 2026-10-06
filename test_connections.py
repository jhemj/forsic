import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import httpx

from forsic_plugin import connections as c, intelligence as ti
from forsic_plugin.evidence import Case
from forsic_plugin.notes import sources
from forsic_plugin.report_driven.host import source_record


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'model': {'provider':'custom','base_url':'http://localhost:11434/v1','default':'qwen'},
                    'plugins':{'entries':{'forsic':{'settings':{'synthetic_roots':['preserve']}}}},
                    'auxiliary': {'title':{'provider':'custom','base_url':'http://localhost:11434/v1','model':'qwen'},
                                  'other':{'provider':'custom','base_url':'http://other/v1','model':'keep'}},
                    'memory':{'memory_enabled':False}, 'agent':{'max_turns':20}}
        self.body = {'llm_base_url':'http://localhost:11434/v1','llm_model':'qwen','jev_base_url':'',
                     'gti_enabled':True,'gti_public_network':False,'gti_api_key':'','clear_gti_key':False}

    def test_public_has_presence_not_credential(self):
        with patch.object(c,'config',return_value=self.cfg), patch.object(c,'key',return_value='secret'):
            value=c.public()
        self.assertTrue(value['gti']['key_configured'])
        self.assertFalse(value['jev']['enabled'])
        self.assertNotIn('secret',json.dumps(value))

    def test_save_only_stages_llm_keeps_other_configuration_and_secret(self):
        with patch.object(c,'config',return_value=self.cfg), patch.object(c,'key',return_value='secret'), \
             patch('hermes_cli.config.save_config') as save, patch('hermes_cli.config.save_env_value') as secret:
            value=c.save({**self.body,'llm_base_url':'https://model.example/v1','llm_model':'new','jev_base_url':'http://local:1234/v1'})
        self.assertTrue(value['llm_pending'])
        self.assertEqual(self.cfg['model']['default'],'qwen')
        self.assertEqual(self.cfg['agent']['max_turns'],20)
        self.assertEqual(self.cfg['plugins']['entries']['forsic']['settings']['synthetic_roots'],['preserve'])
        self.assertEqual(value['jev']['base_url'],'http://local:1234/v1')
        self.assertFalse(value['jev']['enabled'])
        secret.assert_not_called(); save.assert_called_once()

    def test_apply_on_launch_only_matching_auxiliary(self):
        self.cfg['plugins']['entries']['forsic']['settings']['connections']={'llm_pending':{'base_url':'http://new:1234/v1','model':'new'}}
        with patch.object(c,'config',return_value=self.cfg), patch('hermes_cli.config.save_config') as save:
            c.apply_pending_llm()
        self.assertEqual(self.cfg['model']['default'],'new')
        self.assertEqual(self.cfg['auxiliary']['title']['model'],'new')
        self.assertEqual(self.cfg['auxiliary']['other']['model'],'keep')
        self.assertNotIn('llm_pending',c.preferences(self.cfg)); save.assert_called_once()

    def test_key_validation_never_echoes_or_saves_bad_secret(self):
        with patch('hermes_cli.config.save_env_value') as save:
            with self.assertRaises(ValueError) as exc:c.save({**self.body,'gti_api_key':'do-not-echo-this'})
        self.assertNotIn('do-not-echo-this',str(exc.exception));save.assert_not_called()

    def test_explicit_key_removal_disables_lookup(self):
        with patch.object(c,'config',return_value=self.cfg), patch.object(c,'key',return_value=''), \
             patch('hermes_cli.config.save_config'),patch('hermes_cli.config.remove_env_value') as remove:
            result=c.save({**self.body,'clear_gti_key':True})
        remove.assert_called_once_with('VT_APIKEY');self.assertFalse(result['gti']['enabled'])

    def test_key_update_targets_only_vt_and_is_not_returned(self):
        with patch.object(c,'config',return_value=self.cfg), patch.object(c,'key',return_value='a'*64), \
             patch('hermes_cli.config.save_config'),patch('hermes_cli.config.save_env_value') as save, \
             patch('hermes_cli.config.get_env_path') as path:
            value=c.save({**self.body,'gti_api_key':'a'*64})
        save.assert_called_once_with('VT_APIKEY','a'*64)
        path.return_value.chmod.assert_called_once_with(0o600)
        self.assertNotIn('a'*64,json.dumps(value))

    def test_settings_routes_use_no_model_or_network_on_read_save(self):
        from forsic_plugin.dashboard import plugin_api as api
        with patch.object(c,'public',return_value={'jev':{'enabled':False}}), patch.object(ti,'fetch') as fetch:
            self.assertFalse(api.connections_get()['jev']['enabled']);fetch.assert_not_called()
        body=api.ConnectionInput(**{**self.body,'gti_api_key':'b'*64})
        self.assertNotIn('b'*64,repr(body))
        with patch.object(c,'save',return_value={'ok':True}) as save:
            self.assertTrue(api.connections_save(body)['ok'])
            self.assertEqual(save.call_args.args[0]['gti_api_key'],'b'*64)

    def test_urls_no_embedded_secrets(self):
        for url in ['file:///tmp/a','https://key@host/v1','https://host/v1?key=secret','http://a:bad/v1','https://a/#secret','http://a /v1']:
            with self.subTest(url=url),self.assertRaises(ValueError):c.endpoint(url)
        self.assertEqual(c.endpoint('http://[::1]:11434/v1/'),'http://[::1]:11434/v1')

    def test_two_telegram_tokens_use_existing_slots_no_group_changes(self):
        tokens={'telegram_assistant_key':'123456789:'+('x'*35),'telegram_user_key':'987654321:'+('y'*35)}
        with patch.object(c,'config',return_value=self.cfg),patch.object(c,'key',return_value='present'), \
             patch('hermes_cli.config.save_config'),patch('hermes_cli.config.save_env_value') as save, \
             patch('hermes_cli.config.get_env_path'):
            value=c.save({**self.body,**tokens})
        self.assertEqual(save.call_count,2)
        self.assertEqual(save.call_args_list[0].args,('TELEGRAM_BOT_TOKEN',tokens['telegram_assistant_key']))
        self.assertEqual(save.call_args_list[1].args,('FORSIC_USER_TELEGRAM_BOT_TOKEN',tokens['telegram_user_key']))
        for token in tokens.values():self.assertNotIn(token,json.dumps(value))
        self.assertTrue(value['telegram']['assistant_key_configured'])
        self.assertTrue(value['telegram']['user_key_configured'])
        self.assertNotIn('telegram',self.cfg)

    def test_invalid_telegram_token_is_rejected_before_any_secret_write(self):
        with patch('hermes_cli.config.save_env_value') as save:
            with self.assertRaises(ValueError) as error:
                c.save({**self.body,'gti_api_key':'b'*64,'telegram_assistant_key':'sensitive-invalid'})
        save.assert_not_called();self.assertNotIn('sensitive-invalid',str(error.exception))

    def test_telegram_tokens_are_preserved_when_blank_and_individually_removable(self):
        with patch.object(c,'config',return_value=self.cfg),patch.object(c,'key',return_value=''), \
             patch('hermes_cli.config.save_config'),patch('hermes_cli.config.save_env_value') as save, \
             patch('hermes_cli.config.remove_env_value') as remove:
            c.save({**self.body,'telegram_assistant_key':'','telegram_user_key':''})
            save.assert_not_called();remove.assert_not_called()
            c.save({**self.body,'clear_telegram_user_key':True})
            remove.assert_called_once_with('FORSIC_USER_TELEGRAM_BOT_TOKEN')

    def test_telegram_group_normalization_without_resolving_destination(self):
        for supplied, expected in [(' -1001234567890 ', '-1001234567890'),
                                   ('-12345', '-12345'), ('@Case_Group', '@case_group'),
                                   ('https://t.me/Case_Group/', '@case_group'),
                                   ('t.me/Case_Group', '@case_group'),
                                   ('https://telegram.me/Case_Group', '@case_group')]:
            with self.subTest(supplied=supplied):
                self.assertEqual(c.telegram_group(supplied), expected)
        for supplied in ('12345', '@a', 't.me/Case_Group/123', 'https://t.me/c/123/5',
                         'https://other.test/Case_Group', 'https://key@t.me/Case_Group',
                         'https://t.me/Case_Group?token=secret', '@case group'):
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                c.telegram_group(supplied)

    def test_private_invite_rejected_before_any_config_or_secret_write(self):
        for url in ('https://t.me/+private-invite', 't.me/joinchat/private-invite'):
            with patch('hermes_cli.config.save_config') as cfg, patch('hermes_cli.config.save_env_value') as secret:
                with self.assertRaisesRegex(ValueError, '비공개 초대 링크') as error:
                    c.save({**self.body, 'telegram_group':url, 'gti_api_key':'b'*64})
            cfg.assert_not_called(); secret.assert_not_called()
            self.assertNotIn('private-invite', str(error.exception))

    def test_group_preference_saved_only_old_clients_preserve_and_blank_clears(self):
        with patch.object(c,'config',return_value=self.cfg), patch.object(c,'key',return_value=''), \
             patch('hermes_cli.config.save_config') as save, patch('hermes_cli.config.save_env_value') as secret:
            saved=c.save({**self.body, 'telegram_group':'https://t.me/Case_Group'})
            self.assertEqual(saved['telegram']['group'], '@case_group')
            self.assertNotIn('telegram', self.cfg)
            self.assertEqual(c.save(self.body)['telegram']['group'], '@case_group')
            self.assertEqual(c.save({**self.body,'telegram_group':None})['telegram']['group'], '@case_group')
            self.assertEqual(c.save({**self.body,'telegram_group':''})['telegram']['group'], '')
        self.assertEqual(save.call_count,4); secret.assert_not_called()

    def test_group_route_schema_preserves_omitted_values_and_returns_safe_feedback(self):
        from forsic_plugin.dashboard import plugin_api as api
        self.assertIsNone(api.ConnectionInput(**self.body).telegram_group)
        with patch.object(c,'save',return_value={'telegram':{'group':'@case_group'}}) as save:
            result=api.connections_save(api.ConnectionInput(**self.body,telegram_group='t.me/Case_Group'))
        self.assertEqual(save.call_args.args[0]['telegram_group'],'t.me/Case_Group')
        self.assertEqual(result['telegram']['group'],'@case_group')
        with self.assertRaises(api.HTTPException) as error:
            api.connections_save(api.ConnectionInput(**self.body,telegram_group='t.me/+private-invite'))
        self.assertEqual(error.exception.status_code,400)
        self.assertIn('비공개 초대 링크',error.exception.detail)
        self.assertNotIn('private-invite',error.exception.detail)


class IntelligenceTests(unittest.TestCase):
    def client(self, code=200, body=None):
        self.requests=[]
        def respond(req):
            self.requests.append(req)
            return httpx.Response(code,json=body if body is not None else {'data':{'id':ti.EMPTY_SHA256,'attributes':{'last_analysis_stats':{'malicious':0}}}})
        return httpx.Client(transport=httpx.MockTransport(respond),follow_redirects=False)

    def test_lookup_is_one_get_to_official_host_and_retains_source(self):
        with patch.object(c,'key',return_value='private-key'),patch.object(ti.httpx,'Client',return_value=self.client()) as client:
            value=ti.fetch('file',ti.EMPTY_SHA256)
        self.assertEqual(len(self.requests),1)
        req=self.requests[0]
        self.assertEqual((req.method,req.url.host),('GET','www.virustotal.com'))
        self.assertEqual(req.headers['x-apikey'],'private-key')
        self.assertEqual(req.headers['x-tool'],'forsic');self.assertEqual(req.content,b'')
        self.assertFalse(client.call_args.kwargs['follow_redirects']);self.assertFalse(client.call_args.kwargs['trust_env'])
        self.assertEqual(value['outcome'],'found');self.assertNotIn('private-key',json.dumps(value))
        self.assertEqual(value['source_kind'],'external_intelligence');self.assertIn('lookup_at',value)

    def test_http_error_bodies_are_not_saved_and_404_not_benign(self):
        for code in (401,403,429,500,302,404):
            with self.subTest(code=code),patch.object(c,'key',return_value='key'),patch.object(ti.httpx,'Client',return_value=self.client(code,{'error':'secret-provider-body'})):
                value=ti.fetch('file',ti.EMPTY_SHA256)
            self.assertEqual(value['http_status'],code);self.assertNotIn('secret-provider-body',json.dumps(value))
            self.assertEqual(len(self.requests),1)
            if code==404:
                self.assertEqual(value['outcome'],'not_found');self.assertNotIn('error',value)
            else:self.assertIn('error',value)

    def test_timeout_is_not_retried_or_echoed(self):
        with patch.object(c,'key',return_value='key'),patch.object(ti.httpx,'Client',side_effect=httpx.ReadTimeout('private-key')) as client:
            value=ti.fetch('file',ti.EMPTY_SHA256)
        client.assert_called_once();self.assertEqual(value['delivery'],'unknown');self.assertNotIn('private-key',json.dumps(value))

    def test_missing_key_does_not_send(self):
        with patch.object(c,'key',return_value=''),patch.object(ti.httpx,'Client') as client:
            value=ti.fetch('file',ti.EMPTY_SHA256)
        client.assert_not_called();self.assertEqual(value['delivery'],'not_sent')

    def test_indicators_are_not_urls_commands_or_private_addresses(self):
        for kind,value in [('file','/tmp/evidence'),('file','a'*63),('ip','192.168.0.1'),('ip','127.0.0.1'),('ip','224.0.0.1'),('domain','https://example.com/?token=a'),('domain','customer.internal'),('domain','machine.local'),('domain','a@b.com')]:
            with self.subTest(kind=kind,value=value),self.assertRaises(ValueError):ti.indicator(kind,value)
        self.assertEqual(ti.indicator('domain','EXAMPLE.COM.'),('domains','example.com'))

    def test_settings_gate_network_and_disabled_lookup(self):
        with patch.object(c,'preferences',return_value={}),patch.object(ti,'fetch') as fetch:
            self.assertEqual(ti.lookup(None,{'kind':'file','indicator':ti.EMPTY_SHA256})['delivery'],'not_sent')
            fetch.assert_not_called()
        with patch.object(c,'preferences',return_value={'gti_enabled':True}),patch.object(ti,'fetch') as fetch:
            self.assertEqual(ti.lookup(None,{'kind':'domain','indicator':'example.com'})['delivery'],'not_sent')
            fetch.assert_not_called()

    def test_retained_reference_is_citable_reused_and_not_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'evidence').mkdir()
            path=root/'case.json';path.write_text(json.dumps({'case_id':'synthetic-gti','synthetic':True,'evidence_root':str(root/'evidence'),'output_root':str(root/'results')}))
            case=Case(path)
            with patch.object(c,'preferences',return_value={'gti_enabled':True}),patch.object(c,'key',return_value='key'),patch.object(ti.httpx,'Client',return_value=self.client()):
                first=json.loads(case.invoke('forsic_intel',{'kind':'file','indicator':ti.EMPTY_SHA256}))
                second=json.loads(case.invoke('forsic_intel',{'kind':'file','indicator':ti.EMPTY_SHA256}))
            self.assertEqual(len(self.requests),1);self.assertTrue(second['reused'])
            self.assertEqual(second['reused_from'],first['evidence_id'])
            events=sources(case,[first['evidence_id'],second['evidence_id']])
            a,b=map(source_record,events)
            self.assertEqual(a['source_role'],'reference_material')
            self.assertEqual(a['source_generation_group'],b['source_generation_group'])
            self.assertIn('lookup_at',a['coverage'])
            from forsic_plugin.report_driven.host import current
            state=current(case)
            self.assertTrue(state['scope']['external_transmission_authorized'])
            self.assertIn('GTI',state['scope']['external_transmission_scope'])

    def test_ui_check_sends_no_case_indicator_or_full_report(self):
        with patch.object(ti,'fetch',return_value={'http_status':200,'outcome':'found','report':{'private':'body'},'delivery':'received'}) as fetch:
            result=ti.check_connection()
        fetch.assert_called_once_with('file',ti.EMPTY_SHA256);self.assertNotIn('report',result)

    def test_model_view_preserves_values_and_marks_omissions(self):
        original={'report':{'id':'hash','attributes':{'md5':'actual','last_analysis_results':{'bulky':'x'*10000},'gti_assessment':'x'*4000}},'outcome':'found'}
        view=ti.model_view(original)
        self.assertEqual(view['report']['attributes'],{'md5':'actual'})
        self.assertEqual(view['presented_fields'],['md5'])
        self.assertTrue(view['full_report_retained'])
        self.assertIn('last_analysis_results',original['report']['attributes'])


if __name__=='__main__':unittest.main()
