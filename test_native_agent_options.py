"""Native pre-construction isolation hook; synthetic sessions and no model calls."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class NativeAgentOptionsTests(unittest.TestCase):
    def test_native_first_contact_honors_disabled_personal_memory(self):
        from agent.onboarding import first_contact_turn_note, profile_build_mode, PLAIN_INTRO_NOTE
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.yaml'
            config = {'memory':{'user_profile_enabled':False}, 'onboarding':{'profile_build':'ask'}}
            self.assertEqual(profile_build_mode(config), 'off')
            self.assertEqual(first_contact_turn_note(config, path,
                session_history_empty=True, install_has_prior_sessions=False), PLAIN_INTRO_NOTE)
            self.assertFalse(path.exists())
            self.assertEqual(profile_build_mode({'memory':{'user_profile_enabled':True}}), 'ask')

    def setUp(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from hermes_cli import plugins
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        directory=self.stack.enter_context(tempfile.TemporaryDirectory())
        token=set_hermes_home_override(directory);self.addCleanup(reset_hermes_home_override,token)
        self.stack.enter_context(patch.object(plugins,'_plugin_manager',None))
        self.stack.enter_context(patch.object(plugins,'_plugin_managers_by_home',{}))
        self.stack.enter_context(patch.object(plugins,'discover_plugins'))
        self.manager=plugins.get_plugin_manager()
        self.ctx=plugins.PluginContext(plugins.PluginManifest(name='fixture-isolation'),self.manager)
        self.addCleanup(self.manager.unload,'fixture-isolation')
        self.seen=[]
        self.opts={'enabled_toolsets':['forsic'],'skip_context_files':True,'skip_memory':True,
                   'fallback_model':None,'prefill_messages':None,'ephemeral_system_prompt':'Evidence-free repository builder.',
                   'max_iterations':24}
        def options(**kw):
            self.seen.append(kw)
            return deepcopy(self.opts) if kw['session_id']=='builder' else {}
        self.ctx.register_hook('native_agent_options',options)

    def test_owning_session_hook_is_bounded_and_normal_session_has_no_overrides(self):
        from hermes_cli.plugins_agent_options import native_agent_options
        expected=deepcopy(self.opts)
        self.assertEqual(native_agent_options(session_id='builder',cwd='/synthetic/build',model='configured',provider='local',platform='desktop'),expected)
        self.assertEqual(native_agent_options(session_id='ordinary',cwd=None,model='configured',provider='local',platform='desktop'),{})
        self.assertTrue({'session_id','cwd','model','provider','platform'} <= set(self.seen[0]))
        self.assertNotIn('api_key', self.seen[0])
        self.opts['api_key']='forbidden'
        with self.assertRaisesRegex(ValueError,'unsupported'): native_agent_options(session_id='builder',cwd=None,model='x',provider='local',platform='desktop')

    def test_null_or_failed_constructor_policy_fails_closed(self):
        from hermes_cli.plugins_agent_options import native_agent_options
        self.ctx.register_hook('native_agent_options',lambda **kwargs:None)
        with self.assertRaisesRegex(ValueError,'no decision'):
            native_agent_options(session_id='builder',cwd=None,model='x',provider='local',platform='desktop')

    def test_actual_tui_constructor_receives_isolation_before_agent_creation(self):
        from tui_gateway import server
        captured=[]
        def create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace()
        defaults={'_load_cfg':lambda:{},'_startup_system_prompt':lambda *a:'normal prompt',
                  '_resolve_agent_model_runtime':lambda *a:('configured-model',{'provider':'local','api_key':'SECRET-NOT-FOR-HOOK'}),
                  '_load_provider_routing':lambda:{},'_resolve_agent_platform':lambda x:'desktop',
                  '_load_enabled_toolsets':lambda *a:['forsic','terminal','file','memory'],
                  '_load_disabled_toolsets':lambda:[], '_load_reasoning_config':lambda *a:None,
                  '_load_service_tier':lambda:None,'_get_db':lambda:None,'_load_fallback_model':lambda:'configured-fallback',
                  '_load_prefill_messages':lambda:[{'role':'user','content':'configured prefill'}], '_agent_cbs':lambda *a:{}}
        self.stack.enter_context(patch.dict(server._make_agent.__globals__,defaults))
        self.stack.enter_context(patch('run_agent.AIAgent',side_effect=create))
        for sid in ('builder','ordinary'):
            server._make_agent(sid,sid,session_id=sid,cwd_override='/synthetic/build',context_cwd_is_launch_artifact=False)
        builder,ordinary=captured
        for key,value in self.opts.items(): self.assertEqual(builder[key],value,key)
        self.assertEqual(builder['model'],ordinary['model'])
        self.assertEqual(builder['provider'],ordinary['provider'])
        self.assertEqual(ordinary['enabled_toolsets'],['forsic','terminal','file','memory'])
        self.assertEqual(ordinary['ephemeral_system_prompt'],'normal prompt')
        self.assertEqual(ordinary['fallback_model'],'configured-fallback')
        self.assertEqual(ordinary['prefill_messages'],[{'role':'user','content':'configured prefill'}])
        self.assertNotIn('api_key',self.seen[0])


if __name__=='__main__':unittest.main()
