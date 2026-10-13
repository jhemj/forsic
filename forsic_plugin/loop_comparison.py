"""Comparison only: preserve ledger receipts and full model-visible references."""
import json

READ_TOOLS = frozenset(('forsic_case', 'forsic_list', 'forsic_read', 'forsic_read_bytes',
                       'forsic_search', 'forsic_hash', 'forsic_image_info', 'forsic_image_read',
                       'forsic_image_read_mission', 'forsic_archive_read'))
IMAGE_READ_ACTIONS = frozenset(('volumes','list','read','read_bytes','stat','search','hash','archive_list','archive_read'))
# Top-level transport bookkeeping only. Never recursively strip evidence fields.
RECEIPT_FIELDS = frozenset(('evidence_id','started_id','measurement','mission_context','argument_resolution','presented_spans','citation_scope'))


def project(tool, args, result=None):
    action=args.get('action')
    poll=tool=='forsic_verify' and action=='status'
    readonly=(tool in READ_TOOLS or tool=='forsic_image_files' and action in IMAGE_READ_ACTIONS
              or tool=='forsic_reporting' and action in ('state','gaps','source')
              or tool=='forsic_note' and action in ('list','get') or poll)
    if not readonly:
        return {'deduplicate':False}
    view={'arguments':{k:v for k,v in args.items() if k!='reason'},
          'idempotent':not poll,'repeatable':poll,'deduplicate':False}
    if tool in ('forsic_image_files', 'forsic_image_read', 'forsic_image_read_mission', 'forsic_archive_read'):
        view['failure_group'] = 'forsic_image_files'
    if tool == 'forsic_archive_read':
        view['arguments'] = {'offset': 0, 'length': 4096, **view['arguments']}
    if result is not None:
        try: body=json.loads(result)
        except (ValueError,TypeError): return view
        if isinstance(body,dict):
            view['result']={k:v for k,v in body.items() if k not in RECEIPT_FIELDS}
            if body.get('continuation_kind') == 'retained_search' and body.get('source_ref', {}).get('id') == body.get('evidence_id'):
                view['result'].pop('source_ref', None)
                view['result'].pop('next_call', None)
            # Byte-read presentation references are derived from this receipt,
            # not a new underlying observation. Keep unrelated source references.
            if body.get('source_ref',{}).get('id')==body.get('evidence_id') and ('base64' in body.get('omitted_fields',[]) or body.get('read_tool') == 'forsic_archive_read'):
                view['result'].pop('source_ref',None)
                view['result'].pop('raw_bytes_call',None)
    return view
