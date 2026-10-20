"""A long text request must not pin an entire growing tool conversation.

Only summary generation is stubbed. Splitting, pruning, assembly, task replay
and tool pairing use the real native compressor, with synthetic content.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from agent.context_compressor import (
    ContextCompressor, SUMMARY_PREFIX, _SUMMARY_END_MARKER, _INFLIGHT_TASK_REPLAY_HEADER,
    _content_text_for_contains, _template_visible_role,
)
from agent.model_metadata import estimate_messages_tokens_rough


class LongInflightCompactionTests(unittest.TestCase):
    def compressor(self, **kwargs):
        return ContextCompressor('synthetic', config_context_length=32768,
            threshold_tokens_cap=24000, quiet_mode=True, **kwargs)

    def transcript(self, request):
        messages = [
            {'role':'user', 'content':SUMMARY_PREFIX+'\nEarlier work.\n'+_SUMMARY_END_MARKER,
             '_compressed_summary':True},
            {'role':'assistant', 'content':'Continue with the supplied scope.'},
            {'role':'user', 'content':request, 'message_uid':'active-request'},
        ]
        for i in range(60):
            messages += [
                {'role':'assistant', 'content':'', 'tool_calls':[{'id':f'call-{i}',
                    'type':'function', 'function':{'name':'synthetic_read',
                    'arguments':'{"selection":"' + 'synthetic selection ' * 120 + '"}'}}]},
                {'role':'tool', 'tool_call_id':f'call-{i}', 'content':'synthetic observation ' * 100},
            ]
        return messages

    def test_long_text_scope_is_replayed_whole_after_tool_aligned_compaction(self):
        request = 'Preserve this distinct scope and its conditions.\n' * 60 + 'FINAL SCOPE CONDITION'
        for structured in (False, True):
            with self.subTest(structured=structured):
                content = [{'type':'text','text':request}] if structured else request
                messages = self.transcript(content)
                before = deepcopy(messages)
                compressor = self.compressor()
                with patch.object(compressor, '_generate_summary',
                        return_value=SUMMARY_PREFIX+'\nEarlier observations remain provisional.') as generate:
                    compacted = compressor.compress(messages, current_tokens=31000)
                generate.assert_called_once()
                self.assertEqual(messages, before)
                self.assertLess(estimate_messages_tokens_rough(compacted), estimate_messages_tokens_rough(messages))
                # Native alternation may merge the replay into the summary carrier.
                # Its live request must be complete and after the summary boundary.
                replay = compressor._find_inflight_user_task(compacted)
                self.assertIsNotNone(replay)
                text = _content_text_for_contains(replay['content'])
                self.assertEqual(text.rsplit(_INFLIGHT_TASK_REPLAY_HEADER, 1)[-1].strip(), request)
                if replay.get('_compressed_summary'):
                    self.assertGreater(text.index(_INFLIGHT_TASK_REPLAY_HEADER), text.index(_SUMMARY_END_MARKER))
                self.assertEqual([m for m in compacted if m['role']=='tool'][-1]['tool_call_id'], 'call-59')
                calls = {t['id'] for m in compacted for t in m.get('tool_calls', [])}
                results = {m['tool_call_id'] for m in compacted if m['role']=='tool'}
                self.assertEqual(calls, results)
                roles = [r for m in compacted if (r := _template_visible_role(m)) not in (None, 'system')]
                self.assertTrue(all(a != b for a,b in zip(roles, roles[1:])))

    def test_indivisible_and_explicit_multi_user_anchors_still_win(self):
        for request, kwargs in [
            ('indivisible scope ' * 16000, {}),
            ([{'type':'text','text':'Image context'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,synthetic'}}], {}),
            ('Preserve all these scope conditions. ' * 60, {'min_tail_user_messages':2}),
        ]:
            with self.subTest(kind=type(request).__name__, kwargs=kwargs):
                compressor = self.compressor(**kwargs)
                messages = self.transcript(request)
                if kwargs:
                    messages.insert(-2, {'role':'user','content':'Preserve this second request too.'})
                head = compressor._protect_head_size(messages)
                cut = compressor._find_tail_cut_by_tokens(messages, head)
                self.assertLessEqual(cut, 2)


if __name__ == '__main__':
    unittest.main()
