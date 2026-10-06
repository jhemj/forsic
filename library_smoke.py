"""Opt-in synthetic case-library E2E through native Hermes and private Ollama Qwen."""
import argparse
import json
from pathlib import Path
import time

from forsic_plugin.evidence import Case

parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true')
args = parser.parse_args()
if not args.live:
    parser.error('Use --live for the explicitly approved private Qwen synthetic test')
root = Path(__file__).resolve().parent
session = 'forsic-library-e2e-' + str(time.time_ns())
folder = root / 'state/tests' / session
folder.mkdir(parents=True)
archives = []
for name, label, error, explanation in [
    ('permissions', '합성 사례 · 예약 작업 권한 오류', 'permission_denied', '파일 권한 변경으로 예약 작업이 실패한 가능성'),
    ('storage', '합성 사례 · 예약 작업 저장공간 부족', 'no_space_left', '저장공간 부족으로 예약 작업이 실패한 가능성')]:
    base = folder / name
    (base / 'evidence').mkdir(parents=True)
    (base / 'evidence/cron.log').write_text(f'SYNTHETIC TEST ONLY\n2026-08-10T01:00:00Z service=reports task=daily_export status=started\n2026-08-10T01:00:02Z exit=1 error={error} output=not_created\n')
    manifest = base / 'case.json'
    manifest.write_text(json.dumps({'case_id':session + '-' + name, 'label':label, 'question':'예약 작업이 실패한 이유는 무엇인가?',
                                    'scope':'합성 예약 작업 로그 한 파일', 'synthetic':True, 'evidence_kind':'text',
                                    'evidence_root':str(base/'evidence'), 'output_root':str(base/'results')}))
    case = Case(manifest)
    source = json.loads(case.invoke('forsic_read', {'path':'cron.log'}, session))
    assert 'error' not in source
    note = json.loads(case.invoke('forsic_note', {'action':'save', 'question':'예약 작업이 성공했는가?',
                     'answer':f'작업 시작 뒤 {error}, exit=1, output=not_created가 기록됐다. 로그 범위에서 실패 기록을 확인했다.',
                     'evidence_ids':[source['evidence_id']], 'alternatives':[explanation],
                     'gaps':['변경 승인과 전후 실행은 이 로그만으로 확인하지 못했다.'],
                     'next_checks':['변경 승인 기록과 파일 권한/저장공간 상태를 각각 확인한다.']}, session))
    assert 'error' not in note
    saved = json.loads(case.invoke('forsic_cases', {'action':'save', 'tags':['Linux','예약 작업',error]}, session))
    assert 'error' not in saved
    archives.append(saved)

from run_agent import AIAgent
from hermes_state import SessionDB
from model_tools import get_tool_definitions
from hermes_cli.config import load_config

config = load_config()
from deploy_forsic import private_url
model = config.get('model', {})
if model.get('provider') != 'custom' or config.get('fallback_model'):
    raise RuntimeError('Synthetic test requires the configured private model without fallback')
base_url = private_url(model.get('base_url', ''))
fixture = Case(config['plugins']['entries']['forsic']['settings']['case_file'])
if not fixture.config.get('synthetic'):
    raise RuntimeError('This test requires an operator-selected synthetic case')
from forsic_plugin.intake import Intake
intake = Intake(synthetic_roots=[fixture.root])
intake.before(session, str(fixture.root))
intake.inspect(session, {'path': str(fixture.root)})
current = intake.case(session)
tools = get_tool_definitions(enabled_toolsets=['forsic'], quiet_mode=True)
names = [t['function']['name'] for t in tools]
assert 'forsic_cases' in names and all(n.startswith('forsic_') for n in names)
database = SessionDB()
agent = AIAgent(model=model['default'], provider='custom', base_url=base_url, api_key='ollama',
                max_iterations=12, run_budget_seconds=300, enabled_toolsets=['forsic'], reasoning_config={'enabled':False},
                skip_context_files=True, load_soul_identity=True, skip_memory=True, skip_background_review=True,
                session_id=session, session_db=database, platform='cli', fallback_model=None, quiet_mode=True,
                tool_start_callback=lambda call_id, name, arguments: print('TOOL', name, str(arguments)[:320], flush=True))
prompt = ('이번 합성 E2E에서 현재 사건의 cron.log와 change.txt를 한 번씩 읽고 예약 작업의 시도·성공·승인을 구별해줘. '
          '사례 창고에서 예약 작업과 permission_denied 관련 사례를 검색하고, 가장 관련된 이전 사례를 get으로 열고 source로 당시 원문도 확인해줘. '
          '공통점과 차이점, 재사용할 확인 방법을 간단히 설명하되 같은 공격자/원인이라고 단정하지 마. '
          '현재 사건 근거만 evidence_ids에 넣어 forsic_note로 답을 저장하고 forsic_cases save로 현재 사건도 보관해줘. '
          '기존 같은 질문의 노트를 고칠 때는 현재 revision을 확인해. 보고서·다른 파일·해시는 이번 범위가 아니야. '
          '마지막 답변에는 현재 사건 근거 ID와 참고 사례 archive_id를 구분해 짧게 쓰고 멈춰.')
began = time.time()
print('SESSION', session, flush=True)
try:
    result = agent.run_conversation(prompt)
except KeyboardInterrupt:
    result = {'completed':False, 'error':'Client interrupted; last remote request completion remains unknown.'}
finally:
    agent.close()
    database.close()
with current.connect() as db:
    rows = db.execute('SELECT * FROM events WHERE session=? AND time>=? ORDER BY time,rowid', (session,began)).fetchall()
events = [{**dict(r), 'data':json.loads(r['data'])} for r in rows]
posts = [e['data'] for e in events if e['kind'] == 'post_api_request']
receipt = {'session_id':session, 'synthetic':True, 'model':model['default'], 'elapsed_seconds':time.time()-began,
           'fixtures':archives, 'exposed_tools':names, 'result':result, 'events':events,
           'main_api_responses':len(posts), 'reported_response_models':list({str(p.get('response_model')) for p in posts}),
           'reported_usage':[p.get('usage') for p in posts]}
path = folder / 'receipt.json'
path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
print('RECEIPT', path, flush=True)
print(json.dumps({'elapsed_seconds':receipt['elapsed_seconds'], 'main_api_responses':len(posts), 'completed':result.get('completed'), 'answer':result.get('final_response')},ensure_ascii=False,default=str),flush=True)
