"""Existing GTI reports only. Uses the installed HTTP client, not a second agent."""
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import quote
import httpx
from . import connections

EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
LIMITATION = '외부 평판 조회이며 이 사건의 실행·통신·침해·행위자 귀속을 입증하지 않습니다. 미등록/탐지 0은 정상 판정이 아닙니다.'


def indicator(kind, value):
    value = value.strip()
    if kind == 'file':
        if not re.fullmatch(r'(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', value):
            raise ValueError('파일 내용이나 경로가 아니라 MD5/SHA1/SHA256 해시를 입력해주세요.')
        return 'files', value.lower()
    if kind == 'ip':
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast:
            raise ValueError('사설·내부·예약 IP는 외부 조회하지 않습니다.')
        return 'ip_addresses', str(address)
    if kind == 'domain':
        value = value.rstrip('.').encode('idna').decode('ascii').lower()
        if (len(value) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value)
                or value.endswith(('.local', '.localhost', '.internal', '.lan', '.test', '.invalid', '.onion'))):
            raise ValueError('개인정보·내부 이름·URL 경로가 아닌 공개 도메인만 조회할 수 있어요.')
        return 'domains', value
    raise ValueError('file, ip, domain 중 하나를 선택해주세요.')


def fetch(kind, value):
    collection, value = indicator(kind, value)
    api_key = connections.key()
    if not api_key:
        return {'error': '설정에서 VT/GTI API 키를 등록해주세요.', 'outcome': 'not_configured', 'delivery': 'not_sent'}
    url = connections.VT_URL + '/' + collection + '/' + quote(value, safe='')
    result = {'source_kind': 'external_intelligence', 'provider': 'Google Threat Intelligence / VirusTotal',
              'indicator_type': kind, 'indicator': value, 'lookup_at': time.time(),
              'source_url': url, 'scope': 'Existing report for this exact indicator only', 'limitation': LIMITATION}
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, headers={'x-apikey': api_key, 'x-tool': 'forsic', 'Accept': 'application/json'}) as response:
                result.update(http_status=response.status_code, delivery='received')
                if response.status_code == 404:
                    return {**result, 'outcome': 'not_found', 'meaning': 'GTI에 해당 조회 결과가 없습니다. 정상/무해 판정이 아닙니다.'}
                if response.status_code != 200:
                    messages = {401:'API 키 인증에 실패했어요.', 403:'이 조회를 사용할 권한이 없어요.', 429:'조회 한도에 도달했어요. 잠시 뒤 확인해주세요.'}
                    return {**result, 'outcome': 'unavailable', 'error': messages.get(response.status_code, 'GTI가 조회를 완료하지 못했어요.')}
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        return {**result, 'error':'조회 결과가 너무 커서 보존하지 못했어요.', 'outcome':'unavailable'}
                payload = json.loads(raw)
                data = payload.get('data')
                if not isinstance(data, dict) or not isinstance(data.get('attributes'), dict):
                    raise ValueError('invalid GTI response')
                return {**result, 'outcome': 'found', 'report': data}
    except httpx.HTTPError:
        return {**result, 'outcome':'unavailable', 'delivery':'unknown', 'error':'GTI 응답을 받지 못했어요. 자동 재전송하지 않았어요.'}
    except (ValueError, TypeError):
        return {**result, 'outcome':'unavailable', 'error':'GTI 응답 형식을 확인하지 못했어요.'}


def lookup(case, args):
    prefs = connections.preferences()
    if not prefs.get('gti_enabled'):
        return {'error':'VT/GTI 연결이 꺼져 있어요. 설정에서 켜주세요.', 'delivery':'not_sent'}
    kind = args['kind']
    _, value = indicator(kind, args['indicator'])
    if kind != 'file' and not prefs.get('gti_public_network'):
        return {'error':'IP·도메인 조회는 설정에서 공개 지표 전송을 허용한 뒤 사용할 수 있어요.', 'delivery':'not_sent'}
    # Reuse the retained result in this case. No hidden retry or another model call.
    with case.connect() as db:
        row = db.execute("SELECT id,data FROM events WHERE kind='tool_result' AND json_extract(data,'$.tool')='forsic_intel' AND json_extract(data,'$.indicator_type')=? AND json_extract(data,'$.indicator')=? AND json_extract(data,'$.outcome') IN ('found','not_found') ORDER BY time DESC LIMIT 1", (kind,value)).fetchone()
    if row and not args.get('refresh', False):
        result = json.loads(row['data'])
        result.pop('started_id', None)
        return {**result, 'reused':True, 'reused_from':result.get('reused_from') or row['id']}
    return fetch(kind, value)


def model_view(result):
    """Keep the full received report in the ledger, give Qwen a bounded field view."""
    if not isinstance(result.get('report'), dict):
        return result
    fields = ('md5','sha1','sha256','size','type_description','first_submission_date',
              'last_analysis_date','last_modification_date','last_analysis_stats',
              'reputation','country','as_owner','popular_threat_classification','gti_assessment')
    report = result['report']
    attrs = report['attributes']
    selected = {}
    for name in fields:
        if name in attrs and len(json.dumps(attrs[name], ensure_ascii=False)) <= 3000:
            selected[name] = attrs[name]
    return {**result, 'report':{k:report[k] for k in ('id','type') if k in report} | {'attributes':selected},
            'presented_fields':list(selected), 'full_report_retained':True,
            'detail_access':'forsic_reporting(action=source, source_id=evidence_id) or the source popup. Omitted fields are not absent findings.'}


def check_connection():
    # Explicit UI test: one public, non-case hash; never a model or evidence upload.
    result = fetch('file', EMPTY_SHA256)
    return {k:result[k] for k in ('outcome','http_status','error','delivery','lookup_at') if k in result}
