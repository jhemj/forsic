"""Technical capabilities of retained tool fields; never infer forensic meaning."""
import re


def field_role(data, pointer):
    tool=data.get('tool')
    if data.get('schema')=='forsic.triage.v1':
        return 'metadata'  # Candidate locators/rule metadata are not original text.
    if data.get('schema')=='forsic.sensitive.scan.v1':
        return 'body' if pointer=='/summary_text' or re.fullmatch(r'/findings/\d+/masked_excerpt',pointer) else 'metadata'
    if tool=='forsic_shell':
        return 'body' if pointer in ('/stdout','/stderr') else 'input' if pointer=='/command' else 'metadata'
    if pointer == '/search_text' and tool in ('forsic_search', 'forsic_image_files'):
        return 'input'
    if tool in ('forsic_read','forsic_read_bytes','forsic_image_files') and (pointer=='/text' or re.fullmatch(r'/lines/(0|[1-9][0-9]*)/text',pointer)):
        return 'body'
    if tool in ('forsic_search','forsic_image_files') and re.fullmatch(r'/matches/(0|[1-9][0-9]*)/text',pointer):
        return 'body'
    return 'metadata'


def measurement_view(data):
    if data.get('schema')=='forsic.triage.v1':
        return {'measured': 'Bounded selected-file triage candidates',
                'representation':'derived_triage_candidates','original_evidence':False,
                'coverage_not_established':True,'output_pointer':'/candidates',
                'profile':data.get('profile'),'coverage':data.get('coverage',{}),
                'not_measured':['compromise','unselected files','candidate truth','source adoption'],
                'meaning':'Use candidate source recheck handles to inspect original records. Parser records and rule hits are investigation leads; zero candidates is not absence.'}
    if data.get('schema')=='forsic.sensitive.scan.v1':
        return {'measured':'Local detector candidates in selected input ranges',
                'representation':'derived_masked_detection_result','original_evidence':False,
                'coverage_not_established':True,'output_pointer':'/findings',
                'not_measured':['real-person identity','credential live validity','external disclosure','unselected files'],
                'meaning':'Masked excerpts are derived views. Source coordinates and input roles describe the scan; candidate counts do not establish people or exfiltration.'}
    if data.get('representation') == 'directory_inventory':
        return {'measured': 'Allocated directory entry names and filesystem metadata',
                'representation': 'directory_inventory', 'output_pointer': '/entries',
                'listing_complete': data.get('listing_complete'), 'content_not_measured': True,
                'not_measured': ['entry contents', 'execution or success', 'semantic review']}
    if data.get('tool')=='forsic_shell':
        return {'measured':'Returned container command stdout/stderr',
                'input_pointer':'/command','output_pointers':['/stdout','/stderr'],
                'representation':'derived_command_output','original_evidence':False,
                'coverage_not_established':True,
                'meaning':'Commands can transform evidence or print invented text. Exact output retention does not establish original content, acquisition coverage, execution on the investigated host, or semantic truth.'}
    if data.get('tool') not in ('forsic_search','forsic_image_files') or 'matches' not in data:
        return None
    scope_fields = [k for k in ('scope','scope_note','search_path') if k in data]
    return {'measured':'UTF-8 content literal search within selected acquisition scope',
            'input_pointer':'/search_text','output_pointer':'/matches',
            'page':{k:data.get(k) for k in ('files_completed','files_skipped','files_scanned','coverage_complete','scan_exhausted')},
            'match_count':len(data.get('matches',[])), 'has_next_cursor':bool(data.get('next_cursor')),
            'not_measured':['name inventory','past existence','execution or success'],
            'scope_pointers':['/'+k for k in scope_fields],
            **({'scope_pointer':'/'+scope_fields[0]} if scope_fields else {}), 'meaning':'No cursor means scan exhausted, not full coverage. This is a presentation of retained counters, not new evidence.'}


# Shared by source navigation, observation storage, report and native pruning.
READ_SCOPE_FIELDS = ('representation', 'encoding', 'decode_lossy', 'nul_bytes', 'records_decoded',
    'content_bytes', 'complete_content', 'byte_start', 'byte_end', 'line_start', 'line_end',
    'next_line', 'next_offset', 'complete_file', 'complete_member', 'coverage_complete',
    'scan_exhausted', 'files_skipped', 'files_completed', 'files_scanned', 'bytes_scanned',
    'stop_reason', 'match_type', 'search_text', 'scope',
    'original_evidence', 'coverage_not_established', 'preview_truncated',
    'listing_complete', 'content_not_measured')


def scope_view(data, extra_fields=(), *, text_limit=240):
    """The same bounded returned measurement facts in citation and context views."""
    fields = (*READ_SCOPE_FIELDS, *extra_fields)
    scope = {k: data[k] for k in fields if k in data and
             (data[k] is None or type(data[k]) in (bool, int, float) or
              isinstance(data[k], str) and len(data[k]) <= text_limit)}
    omitted = [k for k in fields if k in data and k not in scope]
    if omitted:scope['omitted_fields'] = omitted
    if 'next_cursor' in data:scope['has_next_cursor'] = data['next_cursor'] is not None
    return scope


def acquisition_context(data, pointer):
    """Returned measurement limits; not an agent's claim or semantic classification."""
    context = {'field_role': field_role(data, pointer), 'tool': data.get('tool', '')}
    scope = scope_view(data)
    if data.get('schema')=='forsic.triage.v1':
        scope.update(representation='derived_triage_candidates',original_evidence=False,
                     coverage_not_established=True,triage_coverage=data.get('coverage',{}))
        context['derivation']={'candidates_only':True,
            'execution_state':data.get('execution',{}).get('state'),
            'meaning':'Candidate navigation and trusted rule metadata, not original evidence or adopted judgment.'}
    elif data.get('schema')=='forsic.sensitive.scan.v1':
        scope.update(representation='derived_masked_detection_result',original_evidence=False,
                     coverage_not_established=True,detector_coverage=data.get('coverage',{}))
        context['derivation']={'output_may_be_generated':True,'masked':True,
            'execution_state':data.get('execution',{}).get('state'),
            'meaning':'Detector candidate and derived masked scalar, not a literal original secret or incident conclusion.'}
    elif data.get('tool')=='forsic_shell':
        scope.update(representation='derived_command_output',original_evidence=False,coverage_not_established=True)
        context['derivation']={'command_pointer':'/command','execution_pointer':'/execution',
            'output_may_be_generated':True,'output_truncated':data.get('raw_output',{}).get('truncated'),
            'capture_complete':data.get('raw_output',{}).get('complete'),
            'execution_state':data.get('execution',{}).get('state'),
            'exit_code':data.get('execution',{}).get('exit_code'),
            'preview_truncated':data.get('preview_truncated',{}),
            'meaning':'Returned command output; original evidence and its coverage are not established by this field.'}
    elif pointer == '/text' and 'base64' in data:
        # Older byte results lack the explicit codec/decoder fields. The retained
        # text is still a byte preview, not a decoded authentication/other record.
        scope.update(representation='byte_preview', records_decoded=False)
    elif re.fullmatch(r'/(lines|matches)/(0|[1-9][0-9]*)/text', pointer):
        scope.setdefault('representation', 'matched_text' if pointer.startswith('/matches/') else 'line_text')
        scope.setdefault('records_decoded', False)
    context['returned_scope'] = scope
    return context
