"""Select an immutable report for the user's approved delivery channel.

Selection does not close the investigation, clear gaps or grant publication
approval. No file content, model call or network request is added to the ledger.
"""
import hashlib
import json
from pathlib import Path
import re

REPORT_NAMES = ('executive.html', 'executive.docx', 'practitioner.html', 'practitioner.docx')


def report_files(output, bundle_id, case_id, snapshot_id):
    if not isinstance(bundle_id, str) or not re.fullmatch('[a-f0-9]{64}', bundle_id):
        raise ValueError('Use the bundle_id returned by forsic_reporting(render)')
    root = Path(output) / ('report-bundle-' + bundle_id)
    manifest_path = root / 'manifest.json'
    if root.is_symlink() or manifest_path.is_symlink():
        raise ValueError('Report symlinks are not deliverable')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema') != 'forsic-report-bundle-1' or manifest.get('bundle_id') != bundle_id
            or manifest.get('case_id') != case_id or manifest.get('snapshot_id') != snapshot_id):
        raise ValueError('Report case, bundle or snapshot does not match')
    files = manifest.get('files', [])
    if len(files) != 4 or {f.get('name') for f in files} != set(REPORT_NAMES):
        raise ValueError('Deliver only the four generated executive/practitioner HTML/Word reports')
    result = []
    for name in REPORT_NAMES:
        record = next(f for f in files if f['name'] == name)
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record.get('bytes'):
            raise ValueError('Report artifact changed or is not a regular file')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.get('sha256'):
            raise ValueError('Report artifact changed')
        result.append({'name': name, 'sha256': record['sha256'], 'bytes': record['bytes']})
    return manifest, result


def finalize(case, state, bundle_id):
    from .host import current, ledger
    snapshot_id = state['meta']['snapshot_id']
    _, files = report_files(case.output, bundle_id, case.config['case_id'], snapshot_id)
    if current(case)['meta']['snapshot_id'] != snapshot_id:
        raise ValueError('Case changed; render the current answers before finalizing')
    data = {'bundle_id': bundle_id, 'snapshot_id': snapshot_id,
            'case_id': case.config['case_id'], 'files': files,
            'selection': 'final_delivery_copy', 'analysis_status': state['status']['investigation'],
            'published': False, 'meaning': '전달용 최종본 선택이며 조사 완료·기관 승인·공백 해소를 뜻하지 않음'}
    existing = next((e for e in reversed(ledger(case)) if e['kind'] == 'report_finalized'
                     and e['data'] == data), None)
    receipt = existing['id'] if existing else case.record('report_finalized', data)
    return {'finalized': True, 'bundle_id': bundle_id, 'snapshot_id': snapshot_id,
            'finalization_receipt': receipt, 'reused': existing is not None,
            'delivery_status': 'ready_for_configured_case_topic', 'published': False,
            'meaning': data['meaning'] + '; 실제 첨부 성공은 Telegram 전송 영수증으로 별도 확인'}
