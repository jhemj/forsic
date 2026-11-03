"""Read-only navigation from retained report receipts; no authoring authority."""
import json


def projection(resolved, state, question_ids):
    from .report_driven.narrative import status, _review_identity, _read_call, _historical_pointer
    sessions = resolved['session_lineage']
    goal = resolved.get('goal')
    if not goal or not sessions or not question_ids:
        return None
    # Read only navigation scalars, never whole report bodies or save contracts.
    fields = ('snapshot_id', 'question_ids', 'section', 'pointer', 'offset', 'next_call')
    def column(owner, path, *, numeric=False):
        value = "json_extract(" + owner + ".data,'$." + path + "')"
        return value if numeric else 'substr(' + value + ',1,1801)'

    with resolved['case'].connect() as db:
        rows = db.execute(
            'SELECT r.id,' + ','.join(column('r', key, numeric=key == 'offset') for key in fields) +
            ',' + ','.join(column('s', 'arguments.' + key) for key in
                          ('question_ids', 'section', 'pointer')) + ',' +
            "json_extract(s.data,'$.arguments.offset'),json_extract(s.data,'$.arguments.expected_revision'),"
            "json_extract(r.data,'$.saved'),"
            "substr((SELECT json_group_array(DISTINCT t.atom) FROM json_tree(r.data,'$.value') t "
            "WHERE t.key='node_id' AND t.type='text' AND json_extract(r.data,'$.complete_value')=1),1,1801),"
            "substr((SELECT json_group_array(DISTINCT t.atom) FROM json_each(r.data,'$.items') i, "
            "json_tree(i.value,'$.value') t WHERE t.key='node_id' AND t.type='text'),1,1801) "
            ",json_extract(r.data,'$.complete_value'),"
            "substr((SELECT json_extract(i.value,'$.value') FROM json_each(r.data,'$.items') i "
            "WHERE json_extract(i.value,'$.key')='node_id' AND json_type(i.value,'$.value')='text' LIMIT 1),1,1801) "
            "FROM events r JOIN events s "
            "ON s.id=json_extract(r.data,'$.started_id') "
            "WHERE r.kind='tool_result' AND s.kind='tool_start' AND s.session=r.session "
            "AND r.session IN (" + ','.join('?' for _ in sessions) + ") "
            "AND s.time>=? AND s.rowid<r.rowid "
            "AND json_extract(s.data,'$.tool')='forsic_reporting' "
            "AND json_extract(r.data,'$.tool')='forsic_reporting' "
            "AND json_extract(s.data,'$.arguments.action')='compose' "
            "AND COALESCE(json_extract(r.data,'$.error'),'')='' "
            "AND COALESCE(json_extract(r.data,'$.execution_rejected'),0)=0 "
            "ORDER BY r.rowid DESC LIMIT 64", (*sessions, goal.created_at)).fetchall()
    ids = sorted(question_ids)
    progress = status(state, ids)
    snapshot = _review_identity(progress)
    def scoped(row):
        receipt_ids = json.loads(row[2] or row[7] or 'null')
        if receipt_ids is None:
            receipt_ids = [q['id'] for q in state['questions']]
        return sorted(receipt_ids) == ids

    nodes = [(group, index, entry['node_id']) for group, field in progress['fields'].items()
             for index, entry in enumerate(field['entries'])]
    node_paths = {'/' + root + '/' + group + '/entries/' + str(index): node
                  for group, index, node in nodes
                  for root in ('fields', 'editable_fields', 'form_fields')}
    delivered = set()
    coverage_partial = len(rows) == 64
    for row in rows:
        if any(isinstance(value, str) and len(value.encode()) > 1800 for value in row):
            coverage_partial = True
            continue
        pointer = row[4] or row[9] or ''
        if (scoped(row) and row[1] == snapshot and not row[12]
                and any(pointer == root or pointer.startswith(root + '/')
                        for root in ('/fields', '/editable_fields', '/form_fields'))):
            delivered.update(json.loads(row[13] or '[]'))
            delivered.update(json.loads(row[14] or '[]'))
            # Older complete node pages expose every member in items[].value
            # rather than one top-level value. Require both complete marker and
            # the delivered node_id member at that exact current node pointer.
            if row[15] == 1 and node_paths.get(pointer) == row[16]:
                delivered.add(row[16])
    delivered.intersection_update(node for _, _, node in nodes)
    remaining = [(group, index, node) for group, index, node in nodes if node not in delivered]
    coverage = {'role': 'delivered_not_semantically_reviewed',
                'delivered_node_count': len(delivered), 'current_node_count': len(nodes),
                'delivered_node_ids': sorted(delivered)[:12],
                'omitted_delivered_node_ids': max(0, len(delivered)-12),
                'receipt_window_limit': 64, 'window_partial': coverage_partial}
    for row in rows:
        if any(isinstance(value, str) and len(value.encode()) > 1800 for value in row):
            return None
        if not scoped(row):
            continue
        # Latest successful receipt for this selection is authoritative navigation.
        # A changed draft/basis invalidates it; do not fall back to older pages.
        if (row[1] != _review_identity(progress)
                or (not row[12] and _historical_pointer(row[9] or '')
                    and row[11] is not None and row[11] != progress['revision'])):
            return None
        call = json.loads(row[6]) if row[6] else None
        if call is not None:
            args = call.get('arguments', {})
            render = args.get('action') == 'render' and progress['ready'] and bool(row[12])
            allowed = {'action', 'question_ids', 'section', 'snapshot_id', 'pointer',
                       'offset', 'limit', 'expected_revision'}
            if (render and set(args) != {'action', 'question_ids'}):
                render = False
            if (call.get('tool') != 'forsic_reporting' or (args.get('action') != 'compose' and not render)
                    or set(args) - allowed or sorted(args.get('question_ids', [])) != ids
                    or args.get('snapshot_id', row[1]) != row[1]
                    or ('expected_revision' in args and args['expected_revision'] != progress['revision'])):
                call = None
        review_complete = progress['review_status'] == 'complete'
        if call and ((review_complete and call['arguments']['action'] == 'compose')
                or (progress['content_complete'] and call['arguments']['action'] == 'compose'
                    and not call['arguments'].get('pointer'))):
            call = None
        if call is None and remaining and not review_complete:
            group, index, _ = remaining[0]
            call = _read_call(progress, '/fields/' + group + '/entries/' + str(index), 'review')
        stage = ('render_required' if progress['ready'] else 'review_saved' if review_complete
                 else 'document_review_required' if progress['content_complete'] else 'authoring_required')
        return {'stage': stage, 'delivered_content': coverage, 'receipt_id': row[0], 'snapshot_id': row[1],
                'draft_revision': progress['draft_revision'], 'review_status': progress['review_status'],
                'last_read': {key: value for key, value in zip(
                    ('section', 'pointer', 'offset'), (row[3] or row[8], row[4] or row[9],
                                                       row[5] if row[5] is not None else row[10]))
                    if value is not None},
                'next_call': call, 'role': 'editorial_navigation_not_evidence_or_approval'}
    return None
