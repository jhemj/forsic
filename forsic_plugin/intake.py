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
GOAL = '''종합 침해 분석: 현재 세션의 증거를 조사하여 가장 유력한 설명·근거·신뢰도·남은 한계가 추적되는 통합보고서를 완성한다.
outcome: 요청 범위의 침해 여부·경과·영향에 대한 현재 답과 중요한 경쟁 설명을 제시한다.
verification: 직접 읽은 근거와 최신 답·경쟁 설명·가정·한계를 비교하고 중요한 미검토 범위의 추가 가치를 판단한다. 관측과 추론을 구별하며 남은 중요한 검사를 숨기지 않는다. forsic_reporting(render)로 통합 HTML 보고서와 같은 문서의 기술 부록을 확인한다. Word는 사용자가 다운로드할 때 같은 snapshot에서 생성한다. 파일 생성은 조사 성공 판정이 아니다.
constraints: 원본은 읽기 전용. 없는 관측·출력은 만들지 않는다. 승인된 모델·도구·권한을 유지하며 새 외부 전송·증거 실행·설치는 하지 않는다.
boundaries: 현재 세션에 연결된 사건과 사용자 요청 범위. 과거 사례는 현재 사건의 근거가 아니다.
stop_when: 사용자 중단을 존중한다. 요청 범위를 다루고 남은 검사의 가치가 낮으면 한계를 남겨 종결할 수 있다. 범위 미충족·자료/도구 차단·예산 종료는 부분 보고서와 재개 조건을 남기며 목표 달성으로 표시하지 않는다.'''



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
    def __init__(self, root=None, synthetic_roots=(), tenant_scope=None):
        from .tenants import current_scope
        self.tenant_scope = tenant_scope if tenant_scope is not None else current_scope()
        if self.tenant_scope:
            expected = Path(self.tenant_scope['intake_root']).resolve()
            if root is not None and Path(root).resolve() != expected:
                raise PermissionError('접수 저장소와 테넌트가 일치하지 않습니다.')
            root = expected
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

    def case(self, session, *, read_only=False):
        state = self.state(session)
        if self.tenant_scope and state.get('manifest'):
            from .tenants import validate_case
            validate_case(self.tenant_scope, state['manifest'])
        return Case(state['manifest'], read_only=read_only) if state.get('manifest') else None

    def before(self, session, message='', parent=''):
        self.messages[session] = str(message or '')
        state = self.state(session)
        # Hermes compaction rotates the session ID; its explicit parent inherits scope.
        if parent and not state.get('manifest'):
            if self.tenant_scope:
                from .tenants import assert_session_scope
                assert_session_scope(self.tenant_scope, parent)
            inherited = self.state(parent)
            if inherited.get('manifest'):
                if self.tenant_scope:
                    from .tenants import validate_case
                    validate_case(self.tenant_scope, inherited['manifest'])
                state = {**inherited, 'inherited_from': parent}
                write_json(self.path(session), state)
        if state.get('manifest') and state['stage'] == 'awaiting_question':
            choice = str(message).strip().rstrip('.!~ ')
            if re.fullmatch(r'(1(?:번)?|종합\s*침해\s*분석)(?:으로)?\s*(?:해줘|진행|시작|부탁해)?', choice):
                state.update(stage='goal_pending', question=OPTIONS[0])
                manifest = Path(state['manifest'])
                config = json.loads(manifest.read_text())
                goal = GOAL
                if config.get('onboarding') is not None:
                    from .onboarding import choices, ENVIRONMENTS
                    selected = choices({k: config['onboarding'][k] for k in ('purpose', 'environment', 'question')})
                    goal += '\n사용자 지정 환경: ' + ENVIRONMENTS[selected['environment']] + ' (증거에서 확인한 사실이 아닙니다).'
                    if selected['question']:
                        goal += '\n사용자 추가 요청: ' + json.dumps(selected['question'], ensure_ascii=False)
                config.update(question=OPTIONS[0], investigation_goal=goal)
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
            return WELCOME + ' If the user supplied a path, first inspect it briefly with forsic_intake. Do not select another case automatically.'
        if state['stage'] == 'goal_pending':
            return 'The user selected comprehensive compromise analysis (option 1). End this reply with a brief Korean explanation of the goal and next action. Tool investigation starts in the next native Hermes /goal turn.'
        if state['stage'] == 'focused_question':
            return 'User question: ' + state['question'] + '. If only option 2, 3 or 4 was selected without a specific target, ask for the target or question in Korean. Investigate a free-form question within its stated scope.'
        if state['stage'] == 'goal_manual':
            return 'This host does not support automatic goal submission. Ask the user in Korean to enter /goal 종합 침해 분석 in the Forsic chat. Do not claim that execution started automatically.'
        if state['stage'] in ('goal_queued', 'goal_active'):
            return 'The session has a selected comprehensive investigation and a bound case. Continue the existing question; follow native goal execution and pause state.'
        return 'Intake stage: ' + state['stage'] + '. Check the current case with forsic_reporting(action="case"). When awaiting a question, show these Korean options verbatim and also accept a free-form question: ' + json.dumps(OPTIONS, ensure_ascii=False)

    def inspect(self, session, args, *, allowed_root=None):
        supplied = str(args.get('path', '')).strip().strip('"\'`')
        literal = r'(?<![\w/.-])' + re.escape(supplied) + r'(?=$|[\s\x27"`)\]])'
        if not supplied or not re.search(literal, self.messages.get(session, '')):
            raise ValueError('먼저 사용자가 대화에서 분석할 전체 경로를 알려주세요.')
        path = Path(supplied).expanduser().resolve(strict=True)
        if allowed_root is not None and not path.is_relative_to(Path(allowed_root)):
            raise PermissionError('원격 접속에서는 설정된 공유 자료 폴더 안에서 선택해주세요.')
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
                      'carving_supported': True, 'carving_backend': 'forsic_shell',
                      'recovery_runtime_verified': False, 'recovery_reference': 'source-triage/references/deleted-recovery.md',
                      'meaning': '접근·형식·목록 확인만 완료. 전체 해시·탐색·분석은 아직 하지 않았습니다.'}
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
        if self.tenant_scope:
            config.update(tenant_id=self.tenant_scope['tenant_id'], profile_name=self.tenant_scope['profile_name'])
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
        goal = self.case(session).config.get('investigation_goal') or GOAL
        accepted = inject('/goal ' + goal)
        state.update(stage='goal_queued' if accepted else 'goal_manual', goal_command='/goal ' + goal)
        write_json(self.path(session), state)
        self.case(session).record('goal_dispatch', {'accepted_by_host': accepted, 'command': '/goal ' + goal,
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
        goal = self.case(session).config.get('investigation_goal') or GOAL
        result = dispatch_goal_command(GoalManager(session_id=session, default_max_turns=budget), goal,
                                       authorize_gate=lambda: 'No shell quality gates in Forsic intake')
        saved = load_goal(session)
        if result.error or not saved or saved.status != 'active':
            raise ValueError('조사 목표를 저장하지 못했습니다. /goal status로 상태를 확인해주세요.')
        state.update(stage='goal_active', goal_command='/goal ' + goal)
        write_json(self.path(session), state)
        self.case(session).record('goal_started', {'engine': 'Hermes GoalManager', 'status': saved.status,
                                                   'goal': saved.goal, 'contract': saved.contract.to_dict()}, session)
        return True
