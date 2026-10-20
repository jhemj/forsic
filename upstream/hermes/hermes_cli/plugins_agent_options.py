"""Bounded constructor options from trusted native plugins, before agent creation."""
import json

_ALLOWED = {'enabled_toolsets', 'skip_context_files', 'skip_memory', 'fallback_model',
            'prefill_messages', 'ephemeral_system_prompt', 'max_iterations'}


def native_agent_options(*, session_id, cwd, model, provider, platform):
    from hermes_cli.plugins import discover_plugins, invoke_hook, iter_hook_callbacks
    discover_plugins()
    callbacks = list(iter_hook_callbacks('native_agent_options'))
    responses = invoke_hook('native_agent_options', session_id=session_id, cwd=cwd,
                            model=model, provider=provider, platform=platform)
    if len(responses) != len(callbacks):
        raise ValueError('A native_agent_options callback failed or returned no decision')
    result = {}
    for value in responses:
        if not isinstance(value, dict) or set(value) - _ALLOWED:
            raise ValueError('native_agent_options returned unsupported constructor options')
        if len(json.dumps(value, ensure_ascii=False)) > 65536:
            raise ValueError('native_agent_options exceeds the constructor context budget')
        for name, setting in value.items():
            if name in ('skip_context_files', 'skip_memory') and type(setting) is not bool:
                raise ValueError(name + ' must be a boolean')
            if name == 'enabled_toolsets' and (not isinstance(setting, list) or len(setting) > 64 or
                    not all(isinstance(item,str) and 0 < len(item) <= 128 for item in setting)):
                raise ValueError('enabled_toolsets must be a bounded string list')
            if name == 'max_iterations' and (type(setting) is not int or not 1 <= setting <= 500):
                raise ValueError('max_iterations must be between 1 and 500')
            if name == 'ephemeral_system_prompt' and setting is not None and (not isinstance(setting,str) or len(setting)>32768):
                raise ValueError('ephemeral_system_prompt must be a bounded string')
            if name == 'fallback_model' and setting is not None and not isinstance(setting,(str,dict,list)):
                raise ValueError('fallback_model must use the native fallback configuration shape')
            if name == 'prefill_messages' and setting is not None and not isinstance(setting,list):
                raise ValueError('prefill_messages must be a list or null')
        result.update(value)
    return result
