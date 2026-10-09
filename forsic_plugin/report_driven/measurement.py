"""Technical capabilities of retained tool fields; never infer forensic meaning."""
import re


def field_role(data, pointer):
    tool=data.get('tool')
    if tool in ('forsic_read','forsic_read_bytes','forsic_image_files') and (pointer=='/text' or re.fullmatch(r'/lines/(0|[1-9][0-9]*)/text',pointer)):
        return 'body'
    if tool in ('forsic_search','forsic_image_files') and re.fullmatch(r'/matches/(0|[1-9][0-9]*)/text',pointer):
        return 'body'
    return 'metadata'


def measurement_view(data):
    if data.get('tool') not in ('forsic_search','forsic_image_files') or 'matches' not in data:
        return None
    return {'measured':'UTF-8 content literal search within selected acquisition scope',
            'input_pointer':'/search_text','output_pointer':'/matches',
            'page':{k:data.get(k) for k in ('files_completed','files_skipped','files_scanned','coverage_complete','scan_exhausted')},
            'match_count':len(data.get('matches',[])), 'has_next_cursor':bool(data.get('next_cursor')),
            'not_measured':['name inventory','past existence','execution or success'],
            'scope_pointer':'/scope','meaning':'No cursor means scan exhausted, not full coverage. This is a presentation of retained counters, not new evidence.'}
