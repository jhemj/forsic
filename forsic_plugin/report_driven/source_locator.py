"""Literal navigation over one immutable retained result, never evidence collection."""
from .model_pages import MAX_BYTES, child, integer, size
from .measurement import field_role

MAX_CHARS = 32768
MAX_NODES = 512
MAX_QUERY_BYTES = 256


def nodes(value, path=''):
    # Iterators keep memory bounded without constructing a flattened result index.
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from nodes(item, child(path, key))
    elif isinstance(value, list):
        for key, item in enumerate(value):
            yield from nodes(item, child(path, key))


def locate(source, data, args):
    query = args['query']
    if not isinstance(query, str) or not query or len(query.encode('utf-8')) > MAX_QUERY_BYTES:
        raise ValueError('query must be a nonempty literal string of at most 256 UTF-8 bytes')
    if 'pointer' in args or 'offset' in args:
        raise ValueError('Use query with locator_cursor, or pointer/offset for exact source paging, not both')
    limit = integer(args, 'limit', 5, 10)
    cursor = args.get('locator_cursor')
    start_node, start_char = 0, 0
    if cursor is not None:
        keys = {'source_id', 'source_version', 'query', 'node', 'character'}
        if not isinstance(cursor, dict) or set(cursor) != keys:
            raise ValueError('Use the unchanged locator_cursor from this source query')
        if any(cursor[k] != v for k, v in [('source_id', source['id']), ('source_version', source['version']), ('query', query)]):
            raise ValueError('locator_cursor belongs to another source, version or literal query')
        start_node = integer(cursor, 'node', 0)
        start_char = integer(cursor, 'character', 0)
    result = {
        'source_ref': {'id': source['id'], 'version': source['version']},
        'source_generation_group': source['source_generation_group'],
        'retained_coverage': source['coverage'] if size(source['coverage']) <= 600 else {'omitted': True, 'reason': 'coverage exceeds navigation budget'},
        'retained_result_call': {'action': 'source', 'source_id': source['id'], 'source_version': source['version'], 'pointer': '', 'limit': 5},
        'view_kind': 'retained_literal_locations', 'matches': [],
        'searched_scope': 'String values of this retained tool result only; keys and non-string values excluded',
        'coordinate_basis': 'UTF-8 bytes within the retained scalar field, not original file/image bytes',
        'meaning': 'Navigation only; not new collection, independent evidence, or proof of absence in original evidence',
        'coverage_call': {'nodes_visited': 0, 'character_start_positions_examined': 0},
        'scan_exhausted': False, 'coverage_complete': False,
        'prior_cursor_region_not_presented': cursor is not None,
        'total_match_count': None,
        'returned_count': 0, 'next_call': None, 'stop_reason': None,
    }

    def finish(node=None, character=0, reason=None):
        result['returned_count'] = len(result['matches'])
        result['stop_reason'] = reason
        result['scan_exhausted'] = node is None
        # A continuation never claims that earlier pages were read or reviewed.
        result['coverage_complete'] = cursor is None and node is None
        result['total_match_count'] = len(result['matches']) if result['coverage_complete'] else None
        if node is not None:
            result['next_call'] = {'action': 'source', 'source_id': source['id'],
                'source_version': source['version'], 'query': query, 'limit': limit,
                'locator_cursor': {'source_id': source['id'], 'source_version': source['version'],
                                   'query': query, 'node': node, 'character': character}}
        if size(result) > MAX_BYTES:
            raise ValueError('Source locator identity exceeds response budget; use exact source paging')
        return result

    seen_start = False
    for index, (pointer, value) in enumerate(nodes(data)):
        if index < start_node:
            continue
        pos = start_char if index == start_node else 0
        seen_start = True
        if pos and (not isinstance(value, str) or pos > len(value)):
            raise ValueError('locator_cursor is outside the retained field')
        if result['coverage_call']['nodes_visited'] >= MAX_NODES:
            return finish(index, pos, 'node_limit')
        result['coverage_call']['nodes_visited'] += 1
        if not isinstance(value, str):
            continue
        while pos < len(value):
            remaining = MAX_CHARS - result['coverage_call']['character_start_positions_examined']
            if remaining <= 0:
                return finish(index, pos, 'character_limit')
            end = min(len(value), pos + remaining)
            # Look ahead only to finish a literal beginning in this scan window.
            hit = value.find(query, pos, min(len(value), end + len(query) - 1))
            if hit < 0 or hit >= end:
                result['coverage_call']['character_start_positions_examined'] += end - pos
                pos = end
                continue
            byte_start = len(value[:hit].encode('utf-8'))
            byte_end = byte_start + len(query.encode('utf-8'))
            match = {'pointer': pointer, 'byte_start': byte_start, 'byte_end': byte_end,
                     'literal': value[hit:hit + len(query)], 'role': field_role(data, pointer),
                     'citation': {'source_id': source['id'], 'source_version': source['version'],
                         'pointer': pointer, 'literal': value[hit:hit + len(query)], 'byte_start': byte_start},
                     'next_call': {'action': 'source', 'source_id': source['id'],
                         'source_version': source['version'], 'pointer': pointer,
                         'offset': byte_start, 'limit': byte_end - byte_start}}
            # Reserve room for continuation identity, even for escaped queries.
            reserve = size({'query': query, 'source_id': source['id'], 'source_version': source['version']}) * 2 + 500
            if size({**result, 'matches': result['matches'] + [match]}) + reserve > MAX_BYTES:
                if not result['matches']:
                    raise ValueError('Match pointer exceeds locator budget; use exact source paging')
                return finish(index, pos, 'response_limit')
            result['matches'].append(match)
            result['coverage_call']['character_start_positions_examined'] += hit - pos + 1
            pos = hit + 1  # Include overlapping literal matches without duplicates on resume.
            if len(result['matches']) >= limit:
                return finish(index, pos, 'match_limit')
    if not seen_start:
        raise ValueError('locator_cursor is beyond the retained result')
    return finish()
