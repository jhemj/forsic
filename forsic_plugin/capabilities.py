"""Small shared effect/comparison descriptions of implemented handlers, not permissions."""
from .image_reader import IMAGE_ACTION_REQUIRED

IMAGE_TOOLS = frozenset(('forsic_image_files', 'forsic_image_read', 'forsic_image_search', 'forsic_image_read_mission', 'forsic_archive_read', 'forsic_archive_relist'))
READ_TOOLS = frozenset(('forsic_case', 'forsic_list', 'forsic_read', 'forsic_read_bytes',
                       'forsic_search', 'forsic_hash', 'forsic_image_info'))


def capability(tool, arguments):
    action = arguments.get('action')
    image = tool in IMAGE_TOOLS and (tool != 'forsic_image_files' or action in IMAGE_ACTION_REQUIRED)
    poll = tool == 'forsic_verify' and action == 'status'
    shell = tool == 'forsic_shell'
    typed_scan = shell and action in ('sensitive_scan', 'triage')
    retained_query = shell and action == 'triage_query'
    lookup = (tool == 'forsic_reporting' and action in ('state', 'gaps', 'source', 'execution', 'review_inbox') or
              tool == 'forsic_note' and action in ('list', 'get'))
    readonly = tool in READ_TOOLS or image or poll or lookup or retained_query
    return {'comparison_readonly': readonly, 'repeatable_poll': poll or tool == 'forsic_reporting' and action == 'execution',
            'target_write': False, 'case_ledger_write': True, 'workspace_write': shell and not retained_query,
            'evidence_read': tool in READ_TOOLS - {'forsic_case'} or image or shell,
            'subprocess': shell and not retained_query or image or tool == 'forsic_image_info' or tool == 'forsic_verify' and action == 'start',
            'network': tool == 'forsic_intel', 'target_code_execution': shell and not typed_scan and not retained_query,
            'meaning': 'Declared handler effects, not execution proof or authority expansion.'}


def image_identity(arguments):
    """Backend-relevant values only; materialize existing reader range defaults."""
    from .literal_search import SEARCH_LIMITS
    action = arguments.get('action')
    value = {k: arguments.get(k) for k in ('path','action','volume_offset')}
    if action != 'volumes':
        value['file_path'] = arguments.get('file_path','/')
    fields = {'list':('offset',), 'read':('start_line',), 'read_bytes':('offset','length'),
              'archive_list':('offset',), 'archive_read':('member_index','offset','length'),
              'search':('text','cursor',*SEARCH_LIMITS)}.get(action,())
    defaults = {'offset':0,'start_line':1,'length':4096,
                **{k:v[0] for k,v in SEARCH_LIMITS.items()}}
    for key in fields:
        value[key] = arguments.get(key, defaults.get(key))
        if key in ('offset','start_line','length'):
            value[key] = max(0 if key=='offset' else 1, int(value[key]))
            if key=='length':value[key]=min(12000,value[key])
    return value
