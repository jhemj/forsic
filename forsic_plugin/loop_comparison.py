"""Comparison only: preserve ledger receipts and full model-visible references."""
import json
from .capabilities import capability, IMAGE_TOOLS
# Top-level transport bookkeeping only. Never recursively strip evidence fields.
RECEIPT_FIELDS = frozenset(('evidence_id','started_id','measurement','mission_context','argument_resolution','presented_spans','citation_scope'))


def project(tool, args, result=None):
    contract=capability(tool,args)
    readonly=contract['comparison_readonly']
    poll=contract['repeatable_poll']
    shell=tool=='forsic_shell'
    if not readonly and not shell:
        return {'deduplicate':False}
    view={'arguments':{k:v for k,v in args.items() if k!='reason'},
          'idempotent':readonly and not poll,'repeatable':poll,'deduplicate':False}
    if shell:
        view['arguments']={'cwd':'/work','timeout_seconds':300,**view['arguments']}
    if tool in IMAGE_TOOLS:
        view['failure_group'] = 'forsic_image_files'
    if tool == 'forsic_archive_read':
        view['arguments'] = {'offset': 0, 'length': 4096, **view['arguments']}
    if result is not None:
        try: body=json.loads(result)
        except (ValueError,TypeError): return view
        if isinstance(body,dict):
            view['result']={k:v for k,v in body.items() if k not in RECEIPT_FIELDS}
            from .report_driven.source_handles import handle_reference
            source_ref = body.get('source_ref')
            if (isinstance(source_ref, dict) and source_ref.get('id') == body.get('evidence_id')
                    and handle_reference(body.get('source_handle')) == source_ref):
                # A self-reference minted for this receipt is transport identity,
                # while references/handles selecting any other source are data.
                view['result'].pop('source_handle', None)
                view['result'].pop('source_ref', None)
            def own_image_ref(value):
                return (isinstance(value, dict) and set(value) == {'id', 'version', 'index'}
                        and isinstance(source_ref, dict) and value.get('id') == body.get('evidence_id')
                        and value.get('version') == source_ref.get('version')
                        and type(value.get('index')) is int and value['index'] >= 0)
            if own_image_ref(body.get('image_ref')):
                view['result'].pop('image_ref', None)
            if isinstance(body.get('files'), list):
                view['result']['files'] = [{k: v for k, v in row.items() if k != 'image_ref'}
                    if isinstance(row, dict) and own_image_ref(row.get('image_ref')) else row for row in body['files']]
            if isinstance(body.get('execution'),dict):
                # A fresh container/receipt is not semantic progress. Keep state,
                # cleanup confirmation and errors, including uncertain completion.
                view['result']['execution']={k:v for k,v in body['execution'].items() if k!='container'}
            if shell and body.get('source_ref',{}).get('id')==body.get('evidence_id'):
                # Shell presentation links point at this receipt, not new output.
                view['result'].pop('source_ref',None)
                view['result'].pop('raw_output_calls',None)
                def navigation_selection(selection):
                    if (not isinstance(selection, dict) or not isinstance(source_ref, dict)
                            or not isinstance(selection.get('source_handle'), str)
                            or handle_reference(selection.get('source_handle')) != source_ref
                            or any(key in selection and selection[key] != source_ref[field]
                                   for key, field in (('source_id', 'id'), ('source_version', 'version')))):
                        return selection
                    # Retain pointers, limits, kinds and every non-identity field.
                    return {k: v for k, v in selection.items() if k != 'source_handle'}

                def navigation_call(call):
                    if (not isinstance(call, dict) or call.get('tool') != 'forsic_reporting'
                            or not isinstance(call.get('arguments'), dict)
                            or call['arguments'].get('action') != 'source'):
                        return call
                    return {**call, 'arguments': navigation_selection(call['arguments'])}

                details = body.get('policy_details_calls')
                if isinstance(details, dict):
                    view['result']['policy_details_calls'] = {key: navigation_call(call)
                        for key, call in details.items()}
                for key in ('command_call', 'stdout_call'):
                    if key in body:
                        view['result'][key] = navigation_call(body[key])
                if 'retained_export_target' in body:
                    view['result']['retained_export_target'] = navigation_selection(body['retained_export_target'])
                if isinstance(body.get('retained_export_targets'), list):
                    view['result']['retained_export_targets'] = [navigation_selection(selection)
                        for selection in body['retained_export_targets']]
            if body.get('continuation_kind') == 'retained_search' and body.get('source_ref', {}).get('id') == body.get('evidence_id'):
                view['result'].pop('source_ref', None)
                view['result'].pop('next_call', None)
            # Byte-read presentation references are derived from this receipt,
            # not a new underlying observation. Keep unrelated source references.
            if body.get('source_ref',{}).get('id')==body.get('evidence_id') and ('base64' in body.get('omitted_fields',[]) or body.get('read_tool') == 'forsic_archive_read'):
                view['result'].pop('source_ref',None)
                view['result'].pop('raw_bytes_call',None)
    return view
