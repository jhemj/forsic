"""Technical capabilities of retained tool fields; never infer forensic meaning."""
import re


def field_role(data, pointer):
    tool=data.get('tool')
    if pointer == '/search_text' and tool in ('forsic_search', 'forsic_image_files'):
        return 'input'
    if tool in ('forsic_read','forsic_read_bytes','forsic_image_files') and (pointer=='/text' or re.fullmatch(r'/lines/(0|[1-9][0-9]*)/text',pointer)):
        return 'body'
    if tool in ('forsic_search','forsic_image_files') and re.fullmatch(r'/matches/(0|[1-9][0-9]*)/text',pointer):
        return 'body'
    return 'metadata'


def measurement_view(data):
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
    'stop_reason', 'match_type', 'search_text', 'scope')


def acquisition_context(data, pointer):
    """Returned measurement limits; not an agent's claim or semantic classification."""
    context = {'field_role': field_role(data, pointer), 'tool': data.get('tool', '')}
    scope = {k: data[k] for k in READ_SCOPE_FIELDS if k in data and
             (data[k] is None or type(data[k]) in (bool, int, float) or
              isinstance(data[k], str) and len(data[k]) <= 240)}
    omitted = [k for k in READ_SCOPE_FIELDS if k in data and k not in scope]
    if omitted:scope['omitted_fields'] = omitted
    if 'next_cursor' in data:scope['has_next_cursor'] = data['next_cursor'] is not None
    if pointer == '/text' and 'base64' in data:
        # Older byte results lack the explicit codec/decoder fields. The retained
        # text is still a byte preview, not a decoded authentication/other record.
        scope.update(representation='byte_preview', records_decoded=False)
    elif re.fullmatch(r'/(lines|matches)/(0|[1-9][0-9]*)/text', pointer):
        scope.setdefault('representation', 'matched_text' if pointer.startswith('/matches/') else 'line_text')
        scope.setdefault('records_decoded', False)
    context['returned_scope'] = scope
    return context
