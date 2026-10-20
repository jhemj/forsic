"""Bounded, read-only inventory of targets in a resolved case/session lineage.

This describes retained tool observations, never analytical review or unseen scope.
SQLite groups the full history; Python never loads complete receipts or all targets.
"""
import json


TOOLS = ('forsic_list', 'forsic_read', 'forsic_read_bytes', 'forsic_search',
         'forsic_hash', 'forsic_image_info', 'forsic_image_files')
STATES = ('listed', 'partial_body', 'body_read', 'read_failed')


def _query(lineage):
    # NOT MATERIALIZED keeps large retained bodies out of an intermediate table.
    # Only scalar coordinates/flags and source pointers reach the aggregation.
    return """
    WITH receipts AS NOT MATERIALIZED (
      SELECT r.id, r.data, json_extract(r.data,'$.tool') tool,
        coalesce(json_extract(r.data,'$.path'),json_extract(s.data,'$.arguments.path')) image_path,
        coalesce(json_extract(r.data,'$.file_path'),json_extract(s.data,'$.arguments.file_path')) file_path,
        coalesce(json_extract(r.data,'$.volume_offset'),json_extract(s.data,'$.arguments.volume_offset')) volume,
        coalesce(json_extract(r.data,'$.action'),json_extract(s.data,'$.arguments.action')) action,
        coalesce(json_extract(r.data,'$.member.member_index'),json_extract(s.data,'$.arguments.member_index')) member_index,
        json_extract(r.data,'$.member.name') member_name,
        (json_extract(r.data,'$.error') IS NOT NULL OR coalesce(json_extract(r.data,'$.exit_code'),0)!=0
          OR coalesce(json_extract(r.data,'$.pending'),0)!=0
          OR json_extract(r.data,'$.execution.state') IN ('failed','cancelled','completion_unknown','running','not_started')) failed,
        (json_type(r.data,'$.lines')='array' OR json_type(r.data,'$.text')='text'
          OR json_type(r.data,'$.base64')='text') has_body
      FROM events r LEFT JOIN events s ON s.id=json_extract(r.data,'$.started_id')
        AND s.kind='tool_start' AND s.session=r.session
      WHERE r.kind='tool_result' AND r.session IN (LINEAGE)
        AND json_extract(r.data,'$.tool') IN (TOOLS)
        AND coalesce(json_extract(r.data,'$.execution_rejected'),0)=0
        AND json_extract(r.data,'$.reused_from') IS NULL
    ), observations AS (
      SELECT CASE WHEN tool='forsic_image_files' AND member_index IS NOT NULL THEN 'archive_member'
                  WHEN tool='forsic_image_files' AND file_path IS NOT NULL THEN 'image_file'
                  WHEN tool IN ('forsic_image_files','forsic_image_info') THEN 'image' ELSE 'file' END kind,
        CASE WHEN tool IN ('forsic_image_files','forsic_image_info') THEN image_path END image,
        volume,
        CASE WHEN tool='forsic_image_files' THEN file_path ELSE image_path END path,
        CASE WHEN tool='forsic_image_files' THEN member_index END member_index,
        CASE WHEN tool='forsic_image_files' THEN member_name END member_name,
        CASE WHEN failed AND (tool IN ('forsic_read','forsic_read_bytes') OR action IN ('read','read_bytes','archive_read')) THEN 'read_failed'
             WHEN NOT coalesce(failed,0) AND has_body AND coalesce(json_extract(data,'$.content_not_measured'),0)=0
               AND (tool IN ('forsic_read','forsic_read_bytes') OR tool='forsic_image_files'
                    AND (action IN ('read','read_bytes','archive_read') OR action IS NULL))
               THEN CASE WHEN coalesce(json_extract(data,'$.complete_member'),json_extract(data,'$.complete_file'),0)=1
                         THEN 'body_read' ELSE 'partial_body' END
             ELSE 'listed' END state,
        CASE WHEN member_index IS NOT NULL AND json_type(data,'$.member')='object'
                  OR member_index IS NULL AND (json_type(data,'$.file_path')='text' OR json_type(data,'$.path')='text')
             THEN id ELSE json_extract(data,'$.started_id') END id,
        CASE WHEN member_index IS NOT NULL THEN CASE WHEN json_type(data,'$.member')='object' THEN '/member' ELSE '/arguments/member_index' END
             WHEN json_type(data,'$.file_path')='text' THEN '/file_path'
             WHEN json_type(data,'$.path')='text' THEN '/path'
             ELSE '/arguments/path' END pointer,
        coalesce(failed,0) failed,
        CASE WHEN tool='forsic_search' OR action='search' THEN 1 ELSE 0 END search_only,
        CASE WHEN json_type(data,'$.entries')='array' OR json_type(data,'$.files')='array'
          THEN CASE WHEN coalesce(json_extract(data,'$.listing_complete'),json_extract(data,'$.coverage_complete'),json_extract(data,'$.complete'),0)=1 THEN 0 ELSE 1 END
          ELSE 0 END partial_listing
      FROM receipts WHERE image_path IS NOT NULL
      UNION ALL
      SELECT CASE WHEN json_type(j.value,'$.member_index') IS NOT NULL THEN 'archive_member'
                  WHEN r.tool='forsic_image_files' THEN 'image_file' ELSE 'file' END,
        CASE WHEN r.tool='forsic_image_files' THEN image_path END, volume,
        CASE WHEN json_type(j.value,'$.member_index') IS NOT NULL THEN r.file_path ELSE json_extract(j.value,'$.path') END,
        json_extract(j.value,'$.member_index'),json_extract(j.value,'$.name'),
        'listed',r.id,'/'||a.value||'/'||j.key,0,0,
        CASE WHEN coalesce(json_extract(r.data,'$.listing_complete'),json_extract(r.data,'$.coverage_complete'),json_extract(r.data,'$.complete'),0)=1 THEN 0 ELSE 1 END
      FROM receipts r, json_each('["entries","files"]') a, json_each(r.data,'$.'||a.value) j
      WHERE NOT coalesce(r.failed,0) AND (json_type(j.value,'$.path')='text' OR json_type(j.value,'$.member_index')='integer')
      UNION ALL
      SELECT CASE WHEN r.tool='forsic_image_files' THEN 'image_file' ELSE 'file' END,
        CASE WHEN r.tool='forsic_image_files' THEN image_path END, volume,
        coalesce(json_extract(j.value,'$.file_path'),json_extract(j.value,'$.path')),NULL,NULL,
        CASE WHEN a.value='matches' AND json_type(j.value,'$.text')='text' THEN 'partial_body'
             WHEN a.value='skips' AND json_extract(j.value,'$.reason')='read_failed' THEN 'read_failed' ELSE 'listed' END,
        r.id,'/'||a.value||'/'||j.key,CASE WHEN a.value='skips' THEN 1 ELSE 0 END,1,0
      FROM receipts r, json_each('["matches","skips"]') a, json_each(r.data,'$.'||a.value) j
      WHERE NOT coalesce(r.failed,0) AND coalesce(json_extract(j.value,'$.file_path'),json_extract(j.value,'$.path')) IS NOT NULL
      UNION ALL
      SELECT 'volume',image_path,json_extract(j.value,'$.offset'),NULL,NULL,NULL,
        'listed',r.id,'/volumes/'||j.key,0,0,0
      FROM receipts r,json_each(r.data,'$.volumes') j WHERE NOT coalesce(r.failed,0)
    ), grouped AS (
      SELECT kind,image,volume,path,member_index,min(member_name) member_name,
        max(state='listed') listed,max(state='partial_body') partial_body,
        max(state='body_read') body_read,max(state='read_failed') read_failed,
        max(failed) had_error,max(search_only) search_observed,max(partial_listing) partial_listing,
        count(*) observations,
        min(CASE WHEN state='listed' THEN json_object('source_id',id,'pointer',pointer) END) listed_ref,
        min(CASE WHEN state='partial_body' THEN json_object('source_id',id,'pointer',pointer) END) partial_ref,
        min(CASE WHEN state='body_read' THEN json_object('source_id',id,'pointer',pointer) END) body_ref,
        min(CASE WHEN state='read_failed' THEN json_object('source_id',id,'pointer',pointer) END) failed_ref
      FROM observations GROUP BY kind,image,volume,path,member_index
    ), targets AS (
      SELECT *,CASE WHEN body_read THEN 'body_read' WHEN partial_body THEN 'partial_body'
                    WHEN read_failed THEN 'read_failed' ELSE 'listed' END state FROM grouped
    )
    """.replace('LINEAGE', ','.join('?' for _ in lineage)).replace('TOOLS', ','.join('?' for _ in TOOLS))


def observed_scope(resolved, *, max_targets=120, max_chars=10000):
    """Return exact lineage counts and bounded target/history/reference summaries.

    `resolved` is resolve_context's dictionary and must contain a read-only Case.
    `max_targets` is a nonnegative integer; `max_chars` is at least 1600 and bounds
    the compact JSON serialization (Unicode characters). Oversized identities are
    omitted whole, never clipped or normalized. Errors propagate to the caller.
    body_read means one explicit completed body presentation, not semantic review.
    Partial pages are never combined into an inferred complete body read. `complete`
    means every retained observed target was emitted, never investigation completion.
    """
    if type(max_targets) is not int or max_targets < 0:
        raise ValueError('max_targets must be a nonnegative integer')
    if type(max_chars) is not int or max_chars < 1600:
        raise ValueError('max_chars must be an integer of at least 1600')
    case, lineage = resolved['case'], resolved['session_lineage']
    if not case.read_only or not lineage:
        raise ValueError('Observed scope requires a read-only resolved case and explicit session lineage')
    query = _query(lineage)
    params = (*lineage, *TOOLS)
    body = {'targets': [], 'counts': {s: 0 for s in STATES}, 'omitted': {'targets': 0},
            'complete': False, 'lineage_complete': True,
            'state_meaning': 'listed includes metadata-only targets; body_read is a historical body presentation; read_failed preserves failed body-read attempts.',
            'limitations': [
                'Retained observations only; unseen targets and discovery completeness are unknown.',
                'body_read is a historical completed body presentation, not semantic analysis or a current file version guarantee.',
                'Search matches are partial body observations; search completion, hashes and shell output do not establish body_read.',
                'Partial pages are not combined. Missing image volumes and coordinates remain unknown.',
                'Source refs are representative per observed state; complete histories remain in the case ledger.'],
            'excluded': {'derived_shell_receipts': 0, 'reused_receipts': 0, 'rejected_receipts': 0}}
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    with case.connect() as db:
        db.execute('PRAGMA query_only=ON')
        # Counts and selected rows describe one snapshot even during an active case.
        db.execute('BEGIN')
        for row in db.execute(query + ' SELECT state,count(*) n FROM targets GROUP BY state', params):
            body['counts'][row['state']] = row['n']
        total = sum(body['counts'].values())
        body['counts']['targets'] = total
        for key, predicate in (
            ('derived_shell_receipts', "json_extract(data,'$.tool')='forsic_shell'"),
            ('reused_receipts', "json_extract(data,'$.reused_from') IS NOT NULL"),
            ('rejected_receipts', "coalesce(json_extract(data,'$.execution_rejected'),0)!=0")):
            body['excluded'][key] = db.execute("SELECT count(*) FROM events WHERE kind='tool_result' AND session IN ("+
                ','.join('?' for _ in lineage)+') AND '+predicate, tuple(lineage)).fetchone()[0]
        body['omitted']['targets'] = total
        # Query results stream; neither the distinct identities nor receipt bodies
        # are collected in Python. SQLite may use its normal temporary sort store.
        sql = query + ''' SELECT * FROM targets
            WHERE coalesce(length(image),0)+coalesce(length(path),0)+coalesce(length(member_name),0) <= ?
            ORDER BY kind,image,volume,path,member_index LIMIT ?'''
        for row in db.execute(sql, (*params, max_chars, max_targets)):
            target = {'identity': {'kind': row['kind'], 'image': row['image'], 'volume_offset': row['volume'],
                                  'path': row['path'], 'member_index': row['member_index'], 'member_name': row['member_name']},
                      'state': row['state'], 'observed_states': [s for s in STATES if row[s]],
                      'had_error': bool(row['had_error']), 'search_observed': bool(row['search_observed']),
                      'partial_listing': bool(row['partial_listing']), 'observations': row['observations'],
                      'source_refs': [json.loads(row[key]) for key in ('listed_ref','partial_ref','body_ref','failed_ref') if row[key]]}
            body['targets'].append(target)
            body['omitted']['targets'] -= 1
            if len(encode(body)) > max_chars:
                body['targets'].pop()
                body['omitted']['targets'] += 1
        body['complete'] = body['omitted']['targets'] == 0
    return body
