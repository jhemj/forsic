"""Plugin receipts correlate to trusted native calls, including narrow handler compatibility."""
import json
from tools.registry import registry


def test_execution_identity_reaches_registered_plugin_without_model_override():
    from model_tools import _CallIds, _execute_tool
    seen=[]
    name='synthetic_execution_identity'
    def handler(args, *, session_id='', tool_call_id='', turn_id='', api_request_id='', **kwargs):
        seen.append((args,session_id,tool_call_id,turn_id,api_request_id))
        return json.dumps({'ok':True})
    registry.register(name,'synthetic',{'name':name,'parameters':{'type':'object'}},handler)
    try:
        submitted={'tool_call_id':'model-spoof','session_id':'model-spoof'}
        for owner in ('A','B','A'):
            ids=_CallIds('task-'+owner,'session-'+owner,'call-'+owner,'turn-'+owner,'request-'+owner)
            result=_execute_tool(name,submitted,submitted,ids,user_task=None,enabled_tools=[name],skip_tool_execution_middleware=True)
            assert json.loads(result)['ok']
            assert seen[-1] == (submitted,ids.session_id,ids.tool_call_id,ids.turn_id,ids.api_request_id)
    finally:
        registry.deregister(name)


def test_identity_kwargs_do_not_break_narrow_registered_handlers():
    from model_tools import _CallIds, _execute_tool
    name='synthetic_narrow_identity'
    registry.register(name,'synthetic',{'name':name,'parameters':{'type':'object'}},lambda args:json.dumps(args))
    try:
        submitted={'literal':'unchanged'}
        result=_execute_tool(name,submitted,submitted,_CallIds('task','session','call','turn','request'),
                             user_task=None,enabled_tools=[name],skip_tool_execution_middleware=True)
        assert json.loads(result)==submitted
    finally:
        registry.deregister(name)
