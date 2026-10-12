"""An explicit native reload applies changed schemas under unchanged tool names."""
from contextlib import nullcontext
from copy import deepcopy
import importlib
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tui_gateway import methods_tools
from tools import mcp_tool_agent


class NativeToolReloadTests(unittest.TestCase):
    def check_reload(self, fresh):
        old = [{'type':'function','function':{'name':'fixture_read',
                'parameters':{'type':'object','properties':{}}}}]
        new = deepcopy(old)
        new[0]['function']['parameters']['properties']['path'] = {'type':'string'}
        agent = SimpleNamespace(tools=deepcopy(new if fresh else old), valid_tool_names={'fixture_read'},
            enabled_toolsets=['fixture'], disabled_toolsets=[], platform='tui',
            session_id='synthetic-reload', _session_db=Mock())
        globals_ = {'_sessions_lock':threading.Lock(), '_sessions':{'runtime':{'agent':agent}},
            '_tools_mod':importlib.import_module, '_session_profile_runtime_scope':lambda _:nullcontext(),
            '_load_enabled_toolsets':lambda _:['fixture'], '_load_disabled_toolsets':lambda:[],
            '_emit':Mock(), '_session_info':lambda *_:{}}
        with patch.dict(methods_tools._refresh_live_sessions.__globals__, globals_), \
             patch('model_tools.get_tool_definitions', return_value=new), \
             patch.object(mcp_tool_agent, '_reinject_post_build_tools', return_value=set()), \
             patch.object(mcp_tool_agent, '_reinject_authorized_dynamic_tools'), \
             patch.object(mcp_tool_agent, '_drop_side_agent_tools', side_effect=lambda _, defs, names:(defs,names)):
            methods_tools._refresh_live_sessions()
        self.assertEqual(agent.tools, new)
        self.assertEqual(agent.valid_tool_names, {'fixture_read'})
        args = agent._session_db.update_session_tool_names.call_args.args
        self.assertEqual(args[0], 'synthetic-reload')
        self.assertEqual(args[1]['tools'], new)

    def test_explicit_reload_replaces_same_name_schema_and_persists_pin(self):
        self.check_reload(False)

    def test_fresh_lazy_agent_replaces_old_session_pin_before_next_turn(self):
        self.check_reload(True)


if __name__ == '__main__':
    unittest.main()
