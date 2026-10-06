"""Session-local evidence intake; Hermes still owns conversation and /goal execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from .evidence import Case

WELCOME = '안녕하세요, 포식이예요. 새 조사를 시작하려면 분석할 파일이나 폴더의 전체 경로를 알려주세요.'
OPTIONS = ['종합 침해 분석', '의심 파일·로그 집중 조사', '특정 시간대·계정·행위 확인', '직접 질문하기']
GOAL = '''종합 침해 분석: 현재 세션에 접수한 증거를 처음부터 끝까지 조사하여 근거를 추적할 수 있는 임원용·분석가용 보고서를 완성한다.
outcome: 증거 접수·무결성 확인, 범위별 탐색, 주요 단서와 정상 경쟁 설명 검증, 핵심 시간축, 침해 여부·영향·미확인 범위의 종합 판단과 두 독자 보고서를 완성한다.
verification: forsic-report-driven을 읽고 forsic_note의 주요 질문·현재 답·반론·공백을 forsic_reporting(state/gaps)의 판별 미션과 실제 결과 평가로 연결한다. review-conclusions 절차로 결론과 원문을 대조한다. 현재 답과 남은 공백을 갱신한 뒤 forsic_reporting(render)로 동일 snapshot의 임원용 및 분석가용 HTML/Word 네 파일을 저장한다. 선택적 review는 조언이며 미실행·실패·낡은 검토를 승인으로 쓰지 않는다. 최종 답변에 핵심 근거 전체 ID와 보고서 경로를 제공한다. 파일 생성이나 검토 수신만으로 분석 완료라 하지 않는다. 핵심 공백이 남으면 부분 조사다.
constraints: 원본은 읽기 전용. 명령 기록·실행·성공·승인을 구별한다. 미확인은 부재가 아니다. 부분 검색을 전체 부재로 확정하지 않는다. 과거 사례는 참고일 뿐 현재 증거가 아니다. 새 외부 전송·증거 실행·설치 없이 기존 Ollama와 제공된 도구를 쓴다.
boundaries: forsic_case가 반환하는 현재 세션의 증거 범위와 출력 디렉터리만 사용한다. forensic-investigation 절차를 따라 필요한 하위 절차를 읽는다.
stop_when: 필수 증거나 도구가 없어 핵심 질문을 검증할 수 없거나 동일 실패에서 진전이 없으면 공백·영향·필요 자료를 담은 부분 보고서를 저장하고 사용자에게 요청한다. 목표 달성으로 표시하지 않는다. 사용자 중단은 즉시 존중한다.'''


def write_json(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.intake-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(body, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Intake:
    def __init__(self, root=None, synthetic_roots=()):
        self.root = Path(root or Path(__file__).resolve().parents[1] / 'state/intake')
        self.messages = {}
        self.synthetic_roots = [Path(p).resolve() for p in synthetic_roots]

    def path(self, session):
        if not session:
            raise ValueError('포식이 대화에서 증거를 접수해 주세요.')
        return self.root / (hashlib.sha256(session.encode()).hexdigest() + '.json')

    def state(self, session):
        path = self.path(session)
        return json.loads(path.read_text()) if path.exists() else {'stage': 'awaiting_path'}

    def case(self, session):
        state = self.state(session)
        return Case(state['manifest']) if state.get('manifest') else None

    def before(self, session, message='', parent=''):
        self.messages[session] = str(message or '')
        state = self.state(session)
        # Hermes compaction rotates the session ID; its explicit parent inherits scope.
        if parent and not state.get('manifest'):
            inherited = self.state(parent)
            if inherited.get('manifest'):
                state = {**inherited, 'inherited_from': parent}
                write_json(self.path(session), state)
        if state.get('manifest') and state['stage'] == 'awaiting_question':
            choice = str(message).strip().rstrip('.!~ ')
            if re.fullmatch(r'(1(?:번)?|종합\s*침해\s*분석)(?:으로)?\s*(?:해줘|진행|시작|부탁해)?', choice):
                state.update(stage='goal_pending', question=OPTIONS[0])
                manifest = Path(state['manifest'])
                config = json.loads(manifest.read_text())
                config.update(question=OPTIONS[0], investigation_goal=GOAL)
                write_json(manifest, config)
                write_json(self.path(session), state)
            elif choice:
                question = {'2': OPTIONS[1], '2번': OPTIONS[1], '3': OPTIONS[2], '3번': OPTIONS[2],
                            '4': OPTIONS[3], '4번': OPTIONS[3]}.get(choice, choice)
                state.update(stage='focused_question', question=question)
                manifest = Path(state['manifest'])
                config = json.loads(manifest.read_text())
                config['question'] = question
                write_json(manifest, config)
                write_json(self.path(session), state)
        if state['stage'] == 'awaiting_path':
            return WELCOME + ' 사용자 메시지에 경로가 있으면 forsic_intake로 먼저 가볍게 확인한다. 다른 사건을 자동 선택하지 않는다.'
        if state['stage'] == 'goal_pending':
            return '사용자가 1번 종합 침해 분석을 선택했다. 이번 답변은 목표와 다음 행동을 짧게 안내하고 마친다. 도구 조사는 다음 Hermes /goal 턴에서 시작한다.'
        if state['stage'] == 'focused_question':
            return '사용자 질문: ' + state['question'] + '. 2·3·4번만 선택해서 구체적 대상이 없으면 대상이나 궁금한 점을 물어본다. 자유 질문이면 그 범위를 조사한다.'
        if state['stage'] == 'goal_manual':
            return '자동 goal 접수가 지원되지 않는 호스트입니다. 사용자가 포식이 대화창에 /goal 종합 침해 분석을 입력하도록 안내합니다. 자동 실행됐다고 말하지 않습니다.'
        return '접수 단계: ' + state['stage'] + '. 현재 사건은 forsic_case로 확인한다. 질문 선택 대기이면 다음 선택지를 그대로 안내하되 자유 질문도 받는다: ' + json.dumps(OPTIONS, ensure_ascii=False)

    def inspect(self, session, args):
        supplied = str(args.get('path', '')).strip().strip('"\'`')
        literal = r'(?<![\w/.-])' + re.escape(supplied) + r'(?=$|[\s\x27"`)\]])'
        if not supplied or not re.search(literal, self.messages.get(session, '')):
            raise ValueError('먼저 사용자가 대화에서 분석할 전체 경로를 알려주세요.')
        path = Path(supplied).expanduser().resolve(strict=True)
        if not path.is_file() and not path.is_dir():
            raise ValueError('일반 파일이나 증거 폴더를 선택해주세요.')
        state = self.state(session)
        if state.get('selected_path') == str(path):
            return {**state['inspection'], 'reused': True, 'options': OPTIONS}
        # Changing scope must not silently continue an existing comprehensive goal.
        if state.get('manifest'):
            raise ValueError('이 세션에는 이미 사건이 연결돼 있어요. 다른 증거는 새 세션에서 접수해주세요.')
        selected = []
        inspection = {'selected_path': str(path), 'kind': 'folder' if path.is_dir() else 'file',
                      'integrity': 'not_checked', 'full_content_read': False, 'options': OPTIONS,
                      'tools': {name: bool(shutil.which(name)) for name in ('ewfinfo', 'ewfverify', 'docker')},
                      'carving_supported': False, 'meaning': '접근·형식·목록 확인만 완료. 전체 해시·탐색·분석은 아직 하지 않았습니다.'}
        if path.is_file():
            with path.open('rb') as stream:
                header = stream.read(16)
            selected = [path.name]
            inspection['bytes'] = path.stat().st_size
            ewf = header.startswith(b'EVF\x09\x0d\x0a\xff\x00')
            inspection['format'] = 'EWF' if ewf else ('binary' if b'\x00' in header else 'text_or_other')
            if re.fullmatch(r'\.e\d\d', path.suffix, re.I):
                segments = sorted(p for p in path.parent.iterdir() if p.is_file() and not p.is_symlink()
                                  and p.stem == path.stem and re.fullmatch(r'\.e\d\d', p.suffix, re.I))
                selected = [p.name for p in segments]
                numbers = {int(p.suffix[2:]) for p in segments}
                inspection['segments'] = [{'name': p.name, 'bytes': p.stat().st_size} for p in segments]
                inspection['missing_numbered_segments'] = [n for n in range(1, max(numbers, default=1)+1) if n not in numbers]
                inspection['segment_note'] = '보이는 번호 사이의 누락만 확인했습니다. 마지막 분할까지 모두 있는지는 취득 목록과 대조해야 합니다.'
        else:
            entries = []
            with os.scandir(path) as iterator:
                for entry in iterator:
                    if len(entries) == 50:
                        break
                    entries.append({'name': entry.name, 'directory': entry.is_dir(follow_symlinks=False)})
                else:
                    inspection['listing_complete'] = True
            inspection.setdefault('listing_complete', False)
            inspection['top_level_entries'] = entries
        case_id = 'CASE-' + uuid.uuid4().hex[:12]
        manifest = self.root / 'cases' / case_id / 'case.json'
        config = {'case_id': case_id, 'label': path.name, 'question': '질문 선택 대기',
                  'scope': str(path), 'evidence_kind': inspection.get('format', 'folder'),
                  'synthetic': any(path == p or path.is_relative_to(p) for p in self.synthetic_roots),
                  'evidence_root': str(path if path.is_dir() else path.parent),
                  'output_root': str(manifest.parent / 'results'), 'selected_files': selected}
        # Test/real namespace is operator-owned, not a model-chosen classification.
        write_json(manifest, config)
        case = Case(manifest)
        inspection.update(case_id=case_id, evidence_root=str(case.root), selected_files=selected)
        case.record('intake', inspection, session)
        state = {'stage': 'awaiting_question', 'manifest': str(manifest), 'selected_path': str(path),
                 'inspection': inspection, 'created_at': time.time()}
        write_json(self.path(session), state)
        write_json(self.root / 'current.json', {'manifest': str(manifest), 'session_id': session})
        return inspection

    def after(self, session, inject):
        state = self.state(session)
        if state['stage'] != 'goal_pending':
            return None
        accepted = inject('/goal ' + GOAL)
        state.update(stage='goal_queued' if accepted else 'goal_manual', goal_command='/goal ' + GOAL)
        write_json(self.path(session), state)
        self.case(session).record('goal_dispatch', {'accepted_by_host': accepted, 'command': '/goal ' + GOAL,
                                                  'meaning': 'queued, not proof of execution'}, session)
        return accepted

    def activate_native_goal(self, session):
        """Ink TUI reloads GoalManager after each turn; no replacement agent loop."""
        state = self.state(session)
        if state['stage'] != 'goal_pending':
            return False
        from hermes_cli.goals import GoalManager, load_goal
        from hermes_cli.goal_command import dispatch_goal_command
        from hermes_cli.config import load_config
        budget = int((load_config().get('goals') or {}).get('max_turns', 20))
        result = dispatch_goal_command(GoalManager(session_id=session, default_max_turns=budget), GOAL,
                                       authorize_gate=lambda: 'No shell quality gates in Forsic intake')
        saved = load_goal(session)
        if result.error or not saved or saved.status != 'active':
            raise ValueError('조사 목표를 저장하지 못했습니다. /goal status로 상태를 확인해주세요.')
        state.update(stage='goal_active', goal_command='/goal ' + GOAL)
        write_json(self.path(session), state)
        self.case(session).record('goal_started', {'engine': 'Hermes GoalManager', 'status': saved.status,
                                                   'goal': saved.goal, 'contract': saved.contract.to_dict()}, session)
        return True
