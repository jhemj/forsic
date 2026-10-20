"""Trusted frozen-section IDs and spill budgets reach plugins before composition."""
from copy import deepcopy
from types import SimpleNamespace

from agent.turn_context import _collect_pre_llm_call_context
from hermes_cli import plugins
from hermes_constants import get_hermes_home


def agent():
    return SimpleNamespace(session_id='synthetic',model='offline',platform='cli')


def collect(value,messages=None):
    return _collect_pre_llm_call_context(value,effective_task_id='task',turn_id='turn',
        original_user_message='Continue',messages=messages or [],conversation_history=[])


def test_hook_observes_actual_snapshot_and_same_config_controls_spill(monkeypatch):
    home = get_hermes_home(); home.mkdir(parents=True,exist_ok=True)
    config = home/'config.yaml'
    config.write_text('hooks:\n  output_spill:\n    max_chars: 80\n    preview_head: 12\n    preview_tail: 12\n')
    manager = plugins.PluginManager(); manager._discovered = True
    monkeypatch.setattr(plugins,'get_plugin_manager',lambda:manager)
    def no_render(*args,**kwargs):
        raise AssertionError('Collecting hook metadata must never render sections')
    monkeypatch.setattr(plugins,'render_system_prompt_sections',no_render)
    seen = []
    def hook(**kwargs):
        seen.append(kwargs)
        config.write_text('hooks:\n  output_spill:\n    max_chars: 5000\n')
        return dict(context='retained hook context '*20)
    manager._hooks['pre_llm_call'] = [hook]
    value = agent()
    value._plugin_system_prompt_sections_snapshot = (
        plugins.RenderedPluginSystemPromptSection(id='fixture.frozen',content='Frozen policy',
            position='after_memory',plugin='fixture'),)
    messages = [dict(role='user',content='Original content',api_content='Already sent bytes')]
    before = deepcopy(messages)
    result = collect(value,messages)
    assert seen[0]['system_prompt_section_ids'] == ('fixture.frozen',)
    assert seen[0]['max_context_chars'] == 80
    assert messages == before
    assert 'retained hook context '*20 not in result
    files = list((home/'hook_outputs'/'synthetic').glob('*.txt'))
    assert len(files) == 1
    assert files[0].read_text().strip() == ('retained hook context '*20).strip()


def test_missing_snapshot_and_spill_config_failure_use_safe_metadata(monkeypatch):
    from hermes_cli import lifecycle
    from tools import hook_output_spill
    seen = []
    def hook(name,**kwargs):
        seen.append(kwargs)
        return [dict(context='Complete small context')]
    def unavailable():
        raise OSError('Synthetic config failure')
    monkeypatch.setattr(lifecycle,'invoke_hook',hook)
    monkeypatch.setattr(hook_output_spill,'get_spill_config',unavailable)
    assert collect(agent()) == 'Complete small context'
    assert seen[0]['system_prompt_section_ids'] == ()
    assert seen[0]['max_context_chars'] == 10000
