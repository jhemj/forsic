"""Existing case-event execution boundaries; no scheduler or automatic retry."""
from dataclasses import dataclass
import json
import re
import subprocess
import time
import uuid


@dataclass(frozen=True)
class NativeExecution:
    session_id: str
    turn_id: str
    tool_call_id: str
    api_request_id: str = ''

    def record(self):
        return {key: getattr(self, key) for key in
                ('session_id', 'turn_id', 'tool_call_id', 'api_request_id')}

    def interrupted(self):
        # Native thread-scoped cancellation; never clear or resume its owner.
        from tools.interrupt import is_interrupted
        return is_interrupted()


class ExecutionFailure(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def lookup_call(started_id):
    return {'tool': 'forsic_reporting', 'arguments': {
        'action': 'execution', 'started_id': started_id}}


def execution_view(case, started_id, session_id):
    """Read one exact attempt, including after commit/delivery loss. No backend I/O."""
    start = case.event(started_id)
    if not start or start['kind'] != 'tool_start' or start['session'] != session_id:
        raise ValueError('Select an execution started_id belonging to this conversation')
    with case.connect() as db:
        rows = db.execute("SELECT id,data FROM events WHERE kind='tool_result' "
                          "AND json_extract(data,'$.started_id')=? ORDER BY rowid", (started_id,)).fetchall()
    from .notes import EVIDENCE_TOOLS
    results = []
    for row in rows:
        data = json.loads(row['data'])
        item = {'evidence_id':row['id'], 'execution':data.get('execution'), 'error':bool(data.get('error'))}
        if data.get('tool') in EVIDENCE_TOOLS:
            item['source_call'] = {'tool':'forsic_reporting','arguments':{'action':'source','source_id':row['id']}}
        else:
            item['state_call'] = {'tool':'forsic_reporting','arguments':{'action':'state'}}
        results.append(item)
    with case.connect() as db:
        reconciled = (execution_reconciliation(db, started_id, rows[-1]['id'], session_id)
                      if rows else None)
    recovery = ({'tool':'forsic_reporting','arguments':{'action':'execution_reconcile','started_id':started_id}}
                if rows and start['data'].get('tool') == 'forsic_shell'
                and (results[-1]['execution'] or {}).get('state') == 'completion_unknown' and not reconciled else None)
    return {'execution_started_id': started_id, 'native_call': start['data'].get('native_call'),
            'execution_rejected': start['data'].get('execution_rejected', False),
            'result_commit': 'retained' if rows else 'not_observed', 'results': results,
            'completion': ((results[-1]['execution'] or {}).get('state','not_observed') if rows else 'unknown'),
            'delivery': 'not_observed',
            'process_liveness': 'not_observed',
            'reconciliation': reconciled, 'reconcile_call': recovery,
            'meaning': 'Ledger lookup only. A start without a result proves neither running nor termination. '
                       'Use a retained source when present; missing or uncertain results do not authorize re-execution.'}


def execution_reconciliation(db, started_id, result_id, session_id):
    row = db.execute("SELECT id,data FROM events WHERE kind='execution_reconciled' AND session=? "
                     "AND json_extract(data,'$.started_id')=? AND json_extract(data,'$.result_id')=? "
                     "ORDER BY rowid DESC LIMIT 1", (session_id, started_id, result_id)).fetchone()
    if row:
        value = json.loads(row['data'])
        if (value.get('schema') == 'forsic-shell-termination-1'
                and value.get('observation') == 'recorded_container_absent'
                and value.get('termination_confirmed') is True):
            return {**value, 'receipt_id': row['id']}
    return None


def reconcile_execution(case, started_id, session_id):
    """Confirm an absent recorded container; never stop, replay or adopt its output."""
    view = execution_view(case, started_id, session_id)  # verifies exact ownership
    if view['reconciliation']:
        return view['reconciliation']
    start = case.event(started_id)
    if start['data'].get('tool') != 'forsic_shell' or not view['results']:
        raise ValueError('Only a retained uncertain shell result can be reconciled')
    result = view['results'][-1]
    execution = result['execution'] or {}
    container = execution.get('container', '')
    if (execution.get('state') != 'completion_unknown' or execution.get('backend') != 'docker'
            or execution.get('cleanup', {}).get('client_reaped') is not True
            or not isinstance(container, str) or not re.fullmatch(r'forsic-shell-[0-9a-f]{32}', container)):
        raise ValueError('Recorded shell identity and reaped client are required; uncertainty remains')
    try:
        inspected = subprocess.run(['docker', 'inspect', '--type', 'container', '--format', '{{json .State}}', container],
                                   capture_output=True, timeout=10, check=False)
        missing = inspected.stderr.decode(errors='replace').strip().casefold()
        absent = inspected.returncode != 0 and missing in (
            'error: no such object: '+container, 'error: no such container: '+container,
            'error response from daemon: no such container: '+container)
    except (OSError, subprocess.SubprocessError):
        absent = False
    if not absent:
        raise ValueError('Recorded container absence is not confirmed; execution remains fenced')
    receipt = {'schema':'forsic-shell-termination-1', 'started_id':started_id,
        'result_id':result['evidence_id'], 'container':container,
        'observation':'recorded_container_absent', 'termination_confirmed':True,
        'command_rerun':False, 'original_result_unchanged':True,
        'meaning':'Later lifecycle confirmation only; original output, error and coverage remain unchanged.'}
    with case.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = execution_reconciliation(db, started_id, result['evidence_id'], session_id)
        if existing:
            return existing
        latest = db.execute("SELECT id FROM events WHERE kind='tool_result' AND json_extract(data,'$.started_id')=? "
                            "ORDER BY rowid DESC LIMIT 1", (started_id,)).fetchone()
        if not latest or latest['id'] != result['evidence_id']:
            raise ValueError('Execution result changed during confirmation; read the exact execution again')
        eid = uuid.uuid4().hex
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                   (eid, time.time(), 'execution_reconciled', session_id, json.dumps(receipt)))
    return {**receipt, 'receipt_id':eid}


def pending_image_read(db, arguments, session_id):
    """Fence duplicate unknown work under the same admission transaction, aliases included."""
    from .capabilities import image_identity
    target = image_identity(arguments)
    rows = db.execute("SELECT id,data FROM events WHERE kind='tool_start' AND session=? "
                      "AND json_extract(data,'$.tool')='forsic_image_files' "
                      "AND json_extract(data,'$.arguments.path')=? ORDER BY rowid DESC",
                      (session_id, arguments.get('path')))
    for row in rows:
        start = json.loads(row['data'])
        if start.get('execution_rejected'):
            continue
        try:
            identity = image_identity(start.get('arguments', {}))
        except (ValueError,TypeError):
            continue  # Old malformed inputs cannot equal the valid current target/range.
        if identity != target:
            continue
        returned = db.execute("SELECT data FROM events WHERE kind='tool_result' "
                              "AND json_extract(data,'$.started_id')=? ORDER BY rowid DESC LIMIT 1",
                              (row['id'],)).fetchone()
        if returned is None or json.loads(returned['data']).get('execution', {}).get('state') == 'completion_unknown':
            raise ExecutionFailure('The same image read has an unfinished or uncertain execution. Inspect it before any new attempt.',
                error_code='execution_completion_unknown', pending_started_id=row['id'],
                execution_call=lookup_call(row['id']))


def commit_result(case, result, session_id):
    """Never turn a result-commit exception into a new backend attempt."""
    import sqlite3
    try:
        return case.record('tool_result', result, session_id)
    except (OSError, sqlite3.Error) as exc:
        raise ExecutionFailure('The execution returned but its result commit could not be confirmed. Inspect the exact attempt.',
            error_code='result_commit_unknown', failure_stage='result_commit', error_type=type(exc).__name__,
            started_id=result['started_id'], execution_call=lookup_call(result['started_id']),
            execution_status='unknown') from exc
