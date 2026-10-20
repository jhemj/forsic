import {describe, expect, it} from 'vitest'
import {inspectorEventRuntime, applyInspectorEvent, mergeInspectorHistory, savedToolCalls, skillCatalogue, toolCatalogue} from './forsic-inspector'

describe('passive inspector projections', () => {
  it('maps compression tips to the parent runtime and rejects child or foreign profile events', () => {
    const info = {type: 'session.info', session_id: 'parent', payload: {stored_session_id: 'tip', profile_name: 'A'}}
    expect(inspectorEventRuntime(info, ['ancestor'], 'A')).toBe(null)
    expect(inspectorEventRuntime(info, ['ancestor', 'tip'], 'A')).toBe('parent')
    expect(inspectorEventRuntime(info, ['tip'], 'B')).toBe(null)
    expect(inspectorEventRuntime({type: 'tool.start', session_id: 'child'}, ['tip'], 'A', 'parent')).toBe(null)
    expect(inspectorEventRuntime({type: 'tool.start', session_id: 'parent'}, ['tip'], 'A', 'parent')).toBe('parent')
    expect(inspectorEventRuntime({type: 'session.info', session_id: 'parent', payload: {stored_session_id: 'next-tip', profile_name: 'A'}}, ['tip'], 'A', 'parent')).toBe('parent')
  })
  it('pairs saved native IDs with results and leaves unmatched calls unknown', () => {
    const calls = savedToolCalls([
      {role: 'assistant', tool_calls: [{id: 'a', function: {name: 'read', arguments: '{"path":"sample"}'}}, {id: 'b', function: {name: 'search', arguments: '{}'}}]},
      {role: 'tool', tool_call_id: 'a', content: 'retained result'},
    ])
    expect(calls.map(call => [call.id, call.status])).toEqual([['a', 'completed'], ['b', 'unknown']])
    expect(calls[0].result).toBe('retained result')
    expect(calls[0].arguments).toBe('{"path":"sample"}')
  })
  it('handles a result whose start was outside the history page', () => {
    expect(savedToolCalls([{role: 'tool', tool_call_id: 'old', tool_name: 'read', content: 'result'}])[0]).toMatchObject({id: 'old', name: 'read', status: 'completed'})
  })
  it('does not downgrade completed calls after delayed start delivery', () => {
    const started = applyInspectorEvent([], {type: 'tool.start', payload: {tool_id: 'a', name: 'read', args: {path: 'sample'}}})
    const completed = savedToolCalls([{role: 'tool', tool_call_id: 'a', tool_name: 'read', content: 'done'}])
    const merged = mergeInspectorHistory(completed, started)
    expect(merged[0].status).toBe('completed')
    expect(applyInspectorEvent(merged, {type: 'tool.start', payload: {tool_id: 'a', name: 'read'}})[0].status).toBe('completed')
  })
  it('reports explicit failure and truncation without guessing from plain output', () => {
    const failure = applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'f', name: 'run', result: {exit_code: 2}}})
    expect(failure[0].status).toBe('error')
    const output = savedToolCalls([{role: 'tool', tool_call_id: 'a', content: 'error is a word in this document'}])
    expect(output[0].status).toBe('completed')
    const large = savedToolCalls([{role: 'tool', tool_call_id: 'a', content: 'x'.repeat(13000)}])
    expect(large[0].resultTruncated).toBe(true)
    const formatted = applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'object', name: 'read', result: Array(1800).fill('x')}})
    expect(formatted[0].resultTruncated).toBe(true)
    expect(savedToolCalls([{role: 'assistant', display_kind: 'hidden', tool_calls: [{id: 's', function: {name: 'scaffold'}}]}])).toEqual([])
  })
  it.each([
    ['shell failure', {execution: {state: 'returned', backend: 'docker', exit_code: 2, termination_confirmed: true, cleanup: {exit_code: 0}}, raw_output: {stdout: '', stderr: 'unsupported option'}}, 'error'],
    ['execution failure', {execution: {state: 'failed'}}, 'error'],
    ['cancelled execution', {execution: {state: 'cancelled'}}, 'error'],
    ['explicit unsuccessful result', {success: false, message: 'Request rejected'}, 'error'],
    ['native error', {is_error: true}, 'error'],
    ['result error', {error: 'Tool unavailable'}, 'error'],
    ['failed status', {status: 'failed'}, 'error'],
    ['returned success', {execution: {state: 'returned', exit_code: 0}, raw_output: {stdout: 'error is evidence text', stderr: ''}}, 'completed'],
    ['explicit success', {success: true}, 'completed'],
    ['evidence mentioning failures', {content: {error: 'a recorded historical error', exit_code: 1}, stdout: '{"success":false}', description: 'failed'}, 'completed'],
    ['queued job', {success: true, status: 'queued', job_id: 'synthetic-job'}, 'running'],
    ['accepted job', {status: 'accepted', job_id: 'synthetic-job'}, 'running'],
    ['running execution', {execution: {state: 'running'}}, 'running'],
    ['verification timed out', {job_id: 'synthetic-job', status: 'timed_out', error_type: 'TimeoutExpired', verification_complete: false, finished_at: 1}, 'error'],
    ['verification worker unavailable', {job_id: 'synthetic-job', status: 'worker_unavailable', verification_complete: false, reused: true}, 'unknown'],
    ['verification not started', {status: 'not_started', path: 'synthetic.e01', verification_complete: false}, 'unknown'],
    ['unknown completion', {execution: {state: 'completion_unknown', exit_code: 0, termination_confirmed: false}}, 'unknown'],
    ['unstarted execution', {execution: {state: 'not_started'}}, 'unknown'],
  ])('projects %s identically from saved JSON and live results', (_name, result, status) => {
    const saved = savedToolCalls([{role: 'tool', tool_call_id: 'a', tool_name: 'image_command', content: JSON.stringify(result)}])
    const live = applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'a', name: 'image_command', result}})
    expect(saved[0].status).toBe(status)
    expect(live[0].status).toBe(status)
  })
  it('preserves envelope errors even when result text looks successful', () => {
    expect(savedToolCalls([{role: 'tool', tool_call_id: 'a', is_error: true, content: 'done'}])[0].status).toBe('error')
    expect(applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'a', name: 'read', is_error: true, result: 'done'}})[0].status).toBe('error')
    expect(applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'a', name: 'read', result: {success: true}, result_text: '{"error":"failed operation"}'}})[0].status).toBe('error')
    expect(applyInspectorEvent([], {type: 'tool.complete', payload: {tool_id: 'a', name: 'read', result: {error: 'failed operation'}, result_text: 'done'}})[0].status).toBe('error')
  })
  it('retains returned async and unknown results after late start delivery and hydration', () => {
    for (const result of [{status: 'queued', job_id: 'synthetic-job'}, {execution: {state: 'completion_unknown'}}]) {
      const saved = savedToolCalls([{role: 'tool', tool_call_id: 'a', tool_name: 'verify', content: JSON.stringify(result)}])
      const started = {type: 'tool.start', payload: {tool_id: 'a', name: 'verify', args: {scope: 'sample'}}}
      const live = applyInspectorEvent([], started)
      expect(mergeInspectorHistory(saved, live)[0]).toMatchObject({status: saved[0].status, result: saved[0].result})
      expect(applyInspectorEvent(saved, started)).toEqual(saved)
      expect(applyInspectorEvent(saved, {type: 'tool.complete', payload: {tool_id: 'a', name: 'verify', result: {success: true}}})[0].status).toBe('completed')
    }
  })
  it('keeps older live history outside the bounded page from appearing newest', () => {
    const calls = savedToolCalls(['old', 'overlap', 'new'].map(id => ({role: 'tool', tool_call_id: id, content: id})))
    expect(mergeInspectorHistory(calls.slice(1), calls).map(call => call.id)).toEqual(['overlap', 'new'])
  })
  it('keeps profile summaries honest and excludes disabled entries', () => {
    const tools = toolCatalogue([{name: 'files', label: 'Files', description: 'File operations', enabled: true, tools: ['read', 'read']}, {enabled: false, tools: ['hidden']}])
    expect(tools).toEqual([{name: 'read', description: '', group: 'Files', groupDescription: 'File operations'}])
    expect(skillCatalogue([{name: 'enabled', description: 'Summary', enabled: true}, {name: 'disabled', enabled: false}])).toEqual([{name: 'enabled', description: 'Summary', group: undefined}])
  })
})
