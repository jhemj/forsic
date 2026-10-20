"""Select an immutable report for the user's approved delivery channel.

Selection does not close the investigation, clear gaps or grant publication
approval. No file content, model call or network request is added to the ledger.
"""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

REPORT_NAMES = ('integrated.html', 'integrated.docx')
HTML_REPORT_NAMES = ('integrated.html',)
LEGACY_REPORT_NAMES = ('executive.html', 'executive.docx', 'practitioner.html', 'practitioner.docx')
EVIDENCE_ARCHIVE = 'selected-evidence.zip'
AVAILABILITY_LIMITS = {
    'unavailable': '독립 검토를 수행하거나 보존된 검토 상태를 확인하지 못했습니다.',
    'budget_exhausted': '승인된 독립 검토 호출 예산을 소진하여 현재 최종 답의 추가 검토를 수행하지 못했습니다.',
    'delivery_unconfirmed': '독립 검토 요청은 기록됐지만 완전한 응답의 수신을 확인하지 못했습니다.',
    'incomplete': '독립 검토 응답이 끝까지 도착하지 않았습니다.',
    'invalid_advice': '독립 검토 응답의 정확 인용 또는 형식을 검증하지 못했습니다.'}


def independent_review_projection(review_context, state):
    """Read current receipts/notes; never dispatch or adopt reviewer advice.

    The host supplies enabled/resolved/attempt through trusted routing, never tool
    arguments. The fingerprint binds report disclosure to receipt/disposition
    metadata. A predispatch failure has no receipt; its explicit attempt status
    must therefore be supplied consistently when rendering and selecting delivery.
    """
    if not review_context or not review_context.get('enabled'):
        return None
    from ..independent_review import review_state
    resolved = review_context.get('resolved')
    if not resolved or resolved.get('case_id') != state['meta']['case_id']:
        raise ValueError('Independent review requires the explicitly resolved report case')
    result = review_state(resolved, state)
    keys = ('status', 'review_id', 'latest_stage', 'final_review_current',
            'latest_final_review_id', 'latest_final_status',
            'pending_finding_ids', 'requires_followup', 'dispositions',
            'stale_review_ids', 'historical_review_ids')
    projection = {key: result.get(key) for key in keys}
    if result.get('executive_followup'):
        projection['executive_followup'] = result['executive_followup']
    if review_context.get('executive_followup_required'):
        projection['executive_followup_required'] = True
    projection['enabled'] = True
    # Received attempts are represented by the retained receipt. Other current
    # final receipts supply their own availability status when re-rendered.
    attempt_status = (review_context.get('attempt') or {}).get('status')
    unavailable = (attempt_status if attempt_status in AVAILABILITY_LIMITS else
                   result.get('latest_final_status') if not result.get('final_review_current') else None)
    projection['availability_status'] = unavailable if unavailable in AVAILABILITY_LIMITS else None
    projection['availability_limitation'] = (
        AVAILABILITY_LIMITS[unavailable] + ' 현재 결론은 보존된 근거와 명시된 한계에 의존하며 독립 검토의 승인을 뜻하지 않습니다.'
        if unavailable in AVAILABILITY_LIMITS else '')
    projection['fingerprint'] = hashlib.sha256(json.dumps(projection, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return projection


def _review_gate(case, state, manifest, context):
    projection = independent_review_projection(context, state)
    if projection is None:
        return None, None
    if context['resolved']['case'].manifest != case.manifest:
        raise ValueError('Independent review and report case manifests differ')
    if projection.get('requires_followup') or projection.get('pending_finding_ids'):
        status = 'independent_review_pending'
        next_step = 'review_inbox에서 보존된 중요 의견을 읽고 review_response로 근거와 처분을 저장한 뒤 보고서를 다시 생성하세요.'
    elif ((projection.get('executive_followup_required') or projection.get('executive_followup'))
            and not (projection.get('executive_followup') or {}).get('complete')):
        status = 'independent_review_incomplete'
        next_step = '요청한 임원 독자 3회·15개 질문이 미완료입니다. 가용성 처분은 질문·답의 완료가 아닙니다. 초안을 보존하고 부분 결과와 검토 중단 상태를 알리세요. 미확정 요청은 자동 재전송하지 않습니다.'
    elif not projection.get('final_review_current') and not projection['availability_limitation']:
        status = 'independent_review_required'
        next_step = '현재 답과 근거에 대한 최종 독립 검토를 수행한 뒤 보고서를 다시 생성하세요.'
    elif (manifest.get('independent_review') or {}).get('fingerprint') != projection['fingerprint']:
        status = 'independent_review_rerender_required'
        next_step = '현재 독립 검토 상태와 의견 처분·가용성 한계를 반영하여 보고서를 다시 생성하세요.'
    else:
        return projection, None
    blocked = {'finalized': False, 'delivery_status': status, 'draft_preserved': True,
               'bundle_id': manifest['bundle_id'], 'review': projection, 'next_step': next_step}
    if status == 'independent_review_pending':
        blocked['recovery'] = {'tool':'forsic_reporting','arguments':{'action':'review_inbox'}}
    return projection, blocked


def report_files(output, bundle_id, case_id, snapshot_id=None):
    if not isinstance(bundle_id, str) or not re.fullmatch('[a-f0-9]{64}', bundle_id):
        raise ValueError('Use the bundle_id returned by forsic_reporting(render)')
    root = Path(output) / ('report-bundle-' + bundle_id)
    manifest_path = root / 'manifest.json'
    if root.is_symlink() or manifest_path.is_symlink():
        raise ValueError('Report symlinks are not deliverable')
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError('Report manifest must be an object')
    if not isinstance(manifest.get('snapshot_id'), str) or not re.fullmatch('[a-f0-9]{64}', manifest['snapshot_id']):
        raise ValueError('Invalid report snapshot')
    if (manifest.get('schema') != 'forsic-report-bundle-1' or manifest.get('bundle_id') != bundle_id
            or manifest.get('case_id') != case_id
            or snapshot_id is not None and manifest.get('snapshot_id') != snapshot_id):
        raise ValueError('Report case, bundle or snapshot does not match')
    if manifest.get('manifest_identity') == 'sha256-v1' or (root / '.render-options.json').exists():
        from .host import digest
        if (manifest.get('manifest_identity') != 'sha256-v1'
                or digest({k: v for k, v in manifest.items() if k != 'bundle_id'}) != bundle_id):
            raise ValueError('Report manifest changed')
    elif 'manifest_identity' in manifest:
        raise ValueError('Unsupported report manifest identity')
    from .word_export import FORMATS,validate_inputs
    lazy=manifest.get('document_formats')==FORMATS
    if 'document_formats' in manifest and not lazy:raise ValueError('Unsupported report document format contract')
    allowed_sets=((set(HTML_REPORT_NAMES),set(HTML_REPORT_NAMES)|{EVIDENCE_ARCHIVE}) if lazy else
                  (set(REPORT_NAMES),set(REPORT_NAMES)|{EVIDENCE_ARCHIVE},
                   set(LEGACY_REPORT_NAMES),set(LEGACY_REPORT_NAMES)|{EVIDENCE_ARCHIVE}))
    files = manifest.get('files', [])
    if (not isinstance(files, list) or len(files) not in (1, 2, 3, 4, 5)
            or any(not isinstance(f, dict) or not isinstance(f.get('name'), str) for f in files)
            or len({f['name'] for f in files}) != len(files)
            or {f['name'] for f in files} not in allowed_sets):
        raise ValueError('Invalid report artifacts; only generated reports and selected evidence are supported')
    if lazy:
        inputs=validate_inputs(root,manifest.get('lazy_word'))
        if inputs['view'].get('snapshot_revision')!=manifest['snapshot_id']:
            raise ValueError('Retained Word view snapshot does not match')
    if EVIDENCE_ARCHIVE in {f['name'] for f in files}:
        exported = manifest.get('evidence_export') or {}
        if (manifest.get('redacted') or exported.get('status') != 'complete'
                or exported.get('snapshot_id') != manifest['snapshot_id']):
            raise ValueError('Evidence archive does not match the report export policy or snapshot')
    names={f['name'] for f in files}
    report_names=HTML_REPORT_NAMES if lazy else REPORT_NAMES if set(REPORT_NAMES)<=names else LEGACY_REPORT_NAMES
    result = []
    for name in (*report_names, *([EVIDENCE_ARCHIVE] if EVIDENCE_ARCHIVE in names else [])):
        record = next(f for f in files if f['name'] == name)
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record.get('bytes'):
            raise ValueError('Report artifact changed or is not a regular file')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.get('sha256'):
            raise ValueError('Report artifact changed')
        # The archive is a local, explicit download. Existing finalization and
        # Telegram authorization continue to cover only the report documents.
        if name in report_names:
            result.append({'name': name, 'sha256': record['sha256'], 'bytes': record['bytes']})
    return manifest, result


def finalize(case, state, bundle_id, *, review_context=None, snapshot_id=None, report_scope=None):
    from .host import current
    from .report_reference import resolve_report
    manifest, files, _ = resolve_report(case, state, bundle_id,
        snapshot_id=snapshot_id, report_scope=report_scope)
    snapshot_id = manifest['snapshot_id']
    with ExitStack() as stack:
        if review_context and review_context.get('enabled'):
            from ..independent_review import _goal_key
            resolved = review_context.get('resolved')
            if not resolved or resolved['case'].manifest != case.manifest:
                raise ValueError('Independent review and report case manifests differ')
            # Match the receipt writer's lock order: receipt lock, then case DB.
            # Holding a shared lock excludes request/response writers until the
            # selection commits, without initiating or retrying model work.
            path = case.output / ('independent-review-' + _goal_key(resolved) + '.lock')
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = stack.enter_context(os.fdopen(fd, 'a'))
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'finalized': False, 'delivery_status': 'independent_review_in_progress',
                        'draft_preserved': True, 'bundle_id': bundle_id,
                        'next_step': '독립 검토 기록이 갱신 중입니다. 검토 완료 뒤 보존된 결과를 확인하세요. 같은 검토를 재실행하지 마세요.'}
        with case.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Native note/tool writers cannot commit between this recheck and
            # the selection insert. current() uses read-only connections and
            # sees the latest committed state protected by this writer lock.
            now = current(case)
            # Resolve again under the writer lock. The bundle keeps its exact
            # snapshot even when unrelated native bookkeeping has advanced.
            manifest, files, _ = resolve_report(case, now, bundle_id,
                snapshot_id=snapshot_id, report_scope=report_scope)
            review, blocked = _review_gate(case, now, manifest, review_context)
            if blocked:
                if blocked['delivery_status'] == 'independent_review_rerender_required':
                    from .report_reference import ReportReferenceError, rendering_options
                    recovery = ReportReferenceError(blocked['next_step'], blocked['delivery_status'], now,
                        bundle_id=bundle_id, manifest=manifest,
                        options=rendering_options(case,manifest), report_scope=report_scope).result
                    blocked.update({key: recovery[key] for key in
                        ('recovery','report_reference','current_snapshot_id')})
                return blocked
            data = {'bundle_id': bundle_id, 'snapshot_id': snapshot_id,
                    'case_id': case.config['case_id'], 'files': files,
                    'selection': 'final_delivery_copy', 'analysis_status': now['status']['investigation'],
                    'published': False, 'meaning': '전달용 최종본 선택이며 조사 완료·기관 승인·공백 해소를 뜻하지 않음'}
            if review is not None:
                data['independent_review'] = review
            existing = next((row['id'] for row in db.execute(
                "SELECT id,data FROM events WHERE kind='report_finalized' AND json_extract(data,'$.bundle_id')=? ORDER BY rowid DESC",
                (bundle_id,)) if json.loads(row['data']) == data), None)
            receipt = existing or uuid.uuid4().hex
            if existing is None:
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                           (receipt, time.time(), 'report_finalized', '', json.dumps(data, ensure_ascii=False)))
    result = {'finalized': True, 'bundle_id': bundle_id, 'snapshot_id': snapshot_id,
            'finalization_receipt': receipt, 'reused': existing is not None,
            'delivery_status': 'ready_for_configured_case_topic', 'published': False,
            'meaning': data['meaning'] + '; 실제 첨부 성공은 Telegram 전송 영수증으로 별도 확인'}
    if not manifest.get('redacted'):
        from .delivery import delivery_view
        result['delivery'] = delivery_view(now, [q for q in now['questions'] if q['id'] in manifest['question_ids']],
                                          scope_inventory=manifest.get('scope_inventory'))
    return result
