"""Bounded navigation over retained derived receipts, separate from body reads.

Only typed native triage returns and existing export-declaration selectors enter
this view. No command output is promoted to an original evidence observation.
"""
import json

MAX_RECEIPT_CHARS = 1024 * 1024
MAX_RECEIPTS = 128


class _OversizedReceipt(ValueError):
    pass


def _event(db, source_id):
    size = db.execute('SELECT length(data) FROM events WHERE id=?', (source_id,)).fetchone()
    if size and size[0] > MAX_RECEIPT_CHARS:
        raise _OversizedReceipt('Retained source exceeds the navigation read budget')
    row = db.execute('SELECT * FROM events WHERE id=? AND length(data)<=?',
                     (source_id, MAX_RECEIPT_CHARS)).fetchone()
    return {**dict(row), 'data': json.loads(row['data'])} if row else None


def _navigation(case, event, pointer):
    from .report_driven.host import source_record
    from .report_driven.source_handles import source_handle
    from .report_driven.model_pages import source_read_call
    source = source_record(event)
    handle = source_handle(case, source)
    return {'source_ref': {'id': source['id'], 'version': source['version']},
            'source_handle': handle,
            'read_call': {'tool': 'forsic_reporting',
                          'arguments': source_read_call(source, pointer, handle)}}


def _input(case, db, lineage, value):
    from .notes import EVIDENCE_TOOLS
    from .report_driven.host import source_record
    from .report_driven.model_pages import select
    from .sensitive_policy import export_receipt, workspace_export_scope
    recheck = value.get('source_recheck', {})
    ref = recheck.get('source_ref', {})
    if not isinstance(ref, dict) or not isinstance(ref.get('id'), str):
        raise ValueError('Missing selected-input source')
    event = _event(db, ref['id'])
    if (not event or event['kind'] != 'tool_result' or event['session'] not in lineage
            or event['data'].get('tool') not in EVIDENCE_TOOLS
            or source_record(event)['version'] != ref.get('version')):
        raise ValueError('Selected-input source is outside this retained lineage/version')
    selected = select(event['data'], ref.get('pointer'))
    if not isinstance(selected, str):
        raise ValueError('Selected input must retain its scalar selector')
    kind = recheck.get('selection_kind')
    if kind not in ('evidence_file', 'retained_text', 'workspace_export'):
        raise ValueError('Unknown input selection kind')
    item = {k: value[k] for k in ('input_id', 'state', 'candidate_count', 'records_parsed',
        'records_available', 'records_with_warnings', 'counts_complete', 'reason_codes') if k in value}
    item.update(source_ref=ref, selection_kind=kind,
                **{k: recheck[k] for k in ('sha256', 'bytes') if k in recheck})
    if kind == 'workspace_export':
        workspace_export_scope(event['data'], ref['pointer'])
        receipt = export_receipt(selected, ref.get('export_line'))
        if receipt['sha256'] != recheck.get('sha256'):
            raise ValueError('Selected export declaration hash differs from triage input')
        item['identity'] = {'destination': receipt['destination'],
                            'declared_source': receipt.get('source')}
    elif kind == 'evidence_file':
        item['identity'] = {'selected_path': selected}
    # Retained text content is deliberately absent from this navigation view.
    return item


def _triage(case, db, lineage, event, arguments):
    data = event['data']
    action = data.get('action')
    provenance = data.get('provenance', {})
    if (data.get('schema') != 'forsic.triage.v1'
            or data.get('representation') != 'derived_triage_candidates'
            or action not in ('triage', 'triage_query') or arguments.get('action') != action
            or provenance.get('case_id') != case.config['case_id']
            or provenance.get('session_id') != event['session']
            or (action == 'triage' and data.get('profile') != arguments.get('profile'))
            or (action == 'triage_query' and data.get('scan_id') != arguments.get('scan_id'))):
        raise ValueError('Not a bound typed triage receipt')
    coverage = data.get('coverage', {})
    if coverage.get('state') not in ('complete', 'partial', 'failed', 'skipped'):
        raise ValueError('Unknown selected-input coverage')
    inputs = coverage.get('inputs')
    if not isinstance(inputs, list) or len(inputs) > 64:
        raise ValueError('Invalid selected-input inventory')
    selected = [_input(case, db, lineage, value) for value in inputs]
    item = {'kind': 'triage', **_navigation(case, event, '/coverage'),
            'profile': data.get('profile'), 'scan_id': data.get('scan_id'),
            'coverage': {k: coverage[k] for k in ('state', 'selected_files', 'scope') if k in coverage},
            'summary': {k: data.get('summary', {})[k] for k in (
                'candidate_count', 'returned_candidates', 'omitted_candidates', 'counts_complete')
                if k in data.get('summary', {})},
            'selected_inputs': selected, 'omitted_selected_inputs': 0}
    for key in ('candidate_query', 'selection'):
        if key in data:
            item[key] = data[key]
    return item


def derived_scope(case, lineage, db, *, max_items=12, max_chars=5000):
    """Project one existing read-only snapshot; no staging, file IO or execution.

    Candidate receipt counts cover the full lineage. Verification is bounded to
    128 receipts of at most 1 MiB each, incomplete inputs first then recency; every unexamined/oversized or
    budget-omitted receipt is counted. A selector certifies only a retained export
    declaration; export file integrity and admission require the existing tools.
    """
    predicate = '''r.kind='tool_result' AND r.session IN (LINEAGE)
        AND json_extract(r.data,'$.tool')='forsic_shell'
        AND coalesce(json_extract(r.data,'$.execution_rejected'),0)=0
        AND json_extract(r.data,'$.reused_from') IS NULL
        AND (json_extract(r.data,'$.schema')='forsic.triage.v1'
          OR instr(coalesce(json_extract(r.data,'$.stdout'),''),'exported_bytes')>0
          OR instr(coalesce(json_extract(r.data,'$.stderr'),''),'exported_bytes')>0)'''.replace(
              'LINEAGE', ','.join('?' for _ in lineage))
    total = db.execute('SELECT count(*) FROM events r WHERE '+predicate, tuple(lineage)).fetchone()[0]
    body = {'items': [], 'counts': {'candidate_receipts': total, 'accepted_receipts': 0,
            'rejected_receipts': 0}, 'omitted': {'receipts': total}, 'complete': False,
            'meaning': 'Derived navigation only; triage candidates and selected-input coverage are not original body reads or analytical conclusions. Export selectors verify retained declarations only; file integrity/admission remain unverified.'}
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    rows = db.execute('SELECT r.* FROM events r WHERE '+predicate+
        " AND length(r.data)<=? ORDER BY CASE WHEN json_extract(r.data,'$.coverage.state') IN ('partial','failed','skipped') THEN 0 ELSE 1 END,r.rowid DESC LIMIT ?",
        (*lineage, MAX_RECEIPT_CHARS, MAX_RECEIPTS))
    for row in rows:
        event = {**dict(row), 'data': json.loads(row['data'])}
        data = event['data']
        body['omitted']['receipts'] -= 1
        try:
            start = _event(db, data.get('started_id'))
            if (not start or start['kind'] != 'tool_start' or start['session'] != event['session']
                    or start['data'].get('tool') != 'forsic_shell'):
                raise ValueError('No matching native tool start')
            arguments = start['data'].get('arguments', {})
            if data.get('schema') == 'forsic.triage.v1':
                item = _triage(case, db, lineage, event, arguments)
            else:
                from .model_results import _export_target
                navigation = _navigation(case, event, '/stdout')
                view = {**data, 'source_handle': navigation['source_handle']}
                _export_target(view)
                selectors = ([view['retained_export_target']] if 'retained_export_target' in view
                             else view.get('retained_export_targets', []))
                if not selectors:
                    raise ValueError('No complete retained export declaration')
                navigation['read_call']['arguments']['pointer'] = selectors[0]['pointer']
                item = {'kind': 'workspace_exports', **navigation, 'selectors': selectors,
                        'selector_count': view.get('retained_export_target_count', len(selectors)),
                        'omitted_selectors': view.get('omitted_retained_export_target_count', 0)}
        except _OversizedReceipt:
            body['omitted']['receipts'] += 1
            continue
        except (ValueError, KeyError, TypeError, AttributeError):
            body['counts']['rejected_receipts'] += 1
            continue
        body['counts']['accepted_receipts'] += 1
        # Keep partial/skipped/failed inputs before complete ones when bounded.
        if item['kind'] == 'triage':
            item['selected_inputs'].sort(key=lambda value: value.get('state') == 'complete')
            while item['selected_inputs'] and len(encode(item)) > max_chars-len(encode(body))-20:
                item['selected_inputs'].pop()
                item['omitted_selected_inputs'] += 1
        body['items'].append(item)
        if len(body['items']) > max_items or len(encode(body)) > max_chars:
            body['items'].pop()
            body['omitted']['receipts'] += 1
    body['complete'] = body['omitted']['receipts'] == 0
    return body
