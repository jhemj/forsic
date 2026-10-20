"""Explicit wizard choices and a read-only, bounded evidence folder picker."""
import os
import ipaddress
from pathlib import Path
import stat


PURPOSES = {'comprehensive': '종합 침해 분석', 'focused': '의심 파일·로그 집중 조사',
            'report': '선택한 기존 증거 묶음이나 이전 보고서를 검토하고 보고서를 작성해줘. '
                      'forsic_skill로 forsic-report-driven과 references/audiences.md를 읽고 기존 템플릿과 '
                      'forsic_reporting 도구로 본문과 기술 부록을 포함한 통합 HTML·Word 보고서를 생성해줘. '
                      '직접 확인한 증거와 이전 분석자의 결론·한계를 구별하고 확인하지 않은 결과는 만들지 마.',
            # Preserve legacy wizard metadata and its idempotent request identity.
            'timeline': '자료의 주요 사건과 시간 흐름을 근거에 따라 정리해줘.', 'custom': '직접 질문하기'}
ENVIRONMENTS = {'windows': 'Windows', 'linux': 'Linux', 'macos': 'macOS',
                'mixed': '여러 운영체제', 'unknown': '아직 모름'}


def configured_sources(value=None):
    """Describe an operator-configured location; this does not verify access."""
    if value is None or value == '':
        return {'sources': []}
    if not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
        raise ValueError('설정된 자료 위치를 확인해주세요.')
    path = Path(value.strip())
    if not value.strip() or not path.is_absolute() or '..' in path.parts:
        raise ValueError('설정된 자료 위치를 확인해주세요.')
    path = Path('/' + str(path).lstrip('/'))
    return {'sources': [{'path': str(path), 'name': path.name or str(path)}]}


class EvidenceAccess:
    """Per-request source selection policy, independent of display/forwarded headers."""
    def __init__(self, settings, peer='', *, force_remote=False):
        if not isinstance(settings, dict):
            raise ValueError('설정된 자료 위치를 확인해주세요.')
        sources = configured_sources(settings.get('evidence_directory'))['sources']
        self.path = sources[0]['path'] if sources else ''
        self.label = settings.get('evidence_directory_label') or ''
        flag = settings.get('remote_evidence_directory_only', False)
        if (not isinstance(flag, bool) or not isinstance(self.label, str) or len(self.label) > 4096
                or any(ord(c) < 32 for c in self.label)):
            raise ValueError('설정된 자료 위치를 확인해주세요.')
        try:
            self.remote = not ipaddress.ip_address(peer).is_loopback
        except ValueError:
            self.remote = True
        self.remote = self.remote or force_remote
        self.restricted = flag and self.remote
        self.described = flag or bool(self.label)

    def root(self):
        if not self.path:
            raise PermissionError('공유 자료 폴더가 설정되지 않았습니다.')
        try:
            root = Path(self.path).resolve(strict=True)
            if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
                raise PermissionError('공유 자료 폴더에 접근할 수 없습니다.')
            return root
        except OSError:
            raise PermissionError('공유 자료 폴더에 접근할 수 없습니다.') from None

    def require(self, candidate):
        if self.restricted:
            root = self.root()
            if not candidate.resolve(strict=True).is_relative_to(root):
                raise PermissionError('원격 접속에서는 설정된 공유 자료 폴더 안에서 선택해주세요.')

    def display(self, path):
        if self.path and self.label:
            try:
                suffix = Path(path).relative_to(self.path)
                return self.label.rstrip('/') + ('/' + suffix.as_posix() if suffix.parts else '')
            except ValueError:
                pass
        return str(path)

    def metadata(self):
        try:
            self.root()
            available = True
        except PermissionError:
            available = False
        return {'restricted': self.restricted, 'remote': self.remote, 'default_path': self.path,
                'display_path': self.label or self.path, 'available': available}


def choices(value):
    if not isinstance(value, dict) or set(value) - {'purpose', 'environment', 'question'}:
        raise ValueError('조사 목적과 환경을 확인해주세요.')
    purpose, environment = value.get('purpose'), value.get('environment')
    question = value.get('question', '')
    if (not isinstance(purpose, str) or purpose not in PURPOSES or not isinstance(environment, str)
            or environment not in ENVIRONMENTS or not isinstance(question, str)):
        raise ValueError('조사 목적과 환경을 확인해주세요.')
    question = question.strip()
    if len(question) > 4000 or '\x00' in question:
        raise ValueError('질문은 4,000자 이내로 입력해주세요.')
    if purpose == 'custom' and not question:
        raise ValueError('직접 질문하려면 궁금한 내용을 입력해주세요.')
    # A bare comprehensive choice is the existing Intake.before native trigger.
    # Its optional note and declared environment remain in the case metadata.
    prompt = PURPOSES[purpose]
    if purpose != 'comprehensive':
        if question:
            prompt += '\n사용자 질문: ' + question
        prompt += '\n사용자 지정 환경: ' + ENVIRONMENTS[environment] + ' (증거에서 확인한 사실이 아닙니다).'
    return {'purpose': purpose, 'environment': environment, 'question': question, 'prompt': prompt}


def initial_triage(config, capabilities):
    """Offer discovery at initial question admission, without selecting evidence.

    Wizard labels are user assertions, never inferred OS findings. This pure
    projection neither probes a runtime nor starts a scanner or persistent job.
    """
    selected = config.get('onboarding') or {}
    if (not isinstance(selected, dict) or selected.get('purpose') != 'comprehensive'
            or not capabilities.tool_enabled('forsic_shell')):
        return None
    from .triage_policy import recommendations
    declared = selected.get('environment', 'unknown')
    target = declared if declared in ('windows', 'linux', 'macos') else 'unknown'
    recommendation = recommendations(target, 'quick')
    value = {'role': 'optional_initial_plan', 'declared_environment': declared,
        'environment_basis': 'user_selection_not_observed',
        'state': recommendation['state'], 'profiles': recommendation['profiles'],
        'runtime_verified': False, 'execution_requested': False,
        'lookup': {'tool': 'forsic_shell', 'arguments': {'action': 'capabilities'}},
        'selection': 'Identify the evidence OS/format, then choose useful bounded profiles and explicit retained inputs. '
            'No registered profile is not a clean result. Recheck candidates in source before adopting them; '
            'choose further investigation by value, not scanner ranking alone.'}
    if capabilities.skill_enabled('source-triage'):
        value['procedure'] = {'tool': 'forsic_skill', 'arguments': {'name': 'source-triage'}}
    return value


class EvidenceBrowser:
    limit = 100
    max_entries = 10000

    def __init__(self, home, intake, access=None):
        self.access = access
        self.private = [Path(p).resolve() for p in (home, intake, Path(__file__).resolve().parents[1],
                        '/proc', '/sys', '/dev', '/etc', '/root')]
        self.private += [Path.home() / name for name in ('.ssh', '.aws', '.config', '.codex', '.hermes', '.local')]

    def _private(self, path):
        return any(path == root or path.is_relative_to(root) for root in self.private)

    def _path(self, path, *, default_home=False):
        if not isinstance(path, str) or len(path) > 4096 or '\x00' in path:
            raise ValueError('자료 파일이나 폴더의 전체 경로를 확인해주세요.')
        default = Path(self.access.path) if self.access and self.access.restricted and self.access.path else Path.home()
        candidate = Path(path.strip()) if path.strip() else (default if default_home else None)
        if candidate is None or not candidate.is_absolute() or '..' in candidate.parts:
            raise ValueError('자료 파일이나 폴더의 전체 경로를 확인해주세요.')
        candidate = Path('/' + str(candidate).lstrip('/'))
        if self.access:
            self.access.require(candidate)
        if self._private(candidate):
            raise PermissionError('포식이 저장소나 비공개 설정 폴더는 선택할 수 없습니다.')
        return candidate

    def connection(self, path):
        """Check only the selected entry, without reading contents or creating a case."""
        candidate = self._path(path)
        fd = os.open(candidate.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = candidate.parts[1:]
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            mode = os.fstat(fd).st_mode
            if stat.S_ISDIR(mode):
                if any(root.is_relative_to(candidate) for root in self.private):
                    raise PermissionError('증거와 포식이 저장소는 분리된 폴더를 선택해주세요.')
                kind = 'directory'
            elif stat.S_ISREG(mode):
                kind = 'file'
            else:
                raise ValueError('일반 자료 파일이나 폴더를 선택해주세요.')
            result = {'path': str(candidate), 'name': candidate.name or str(candidate),
                      'kind': kind, 'access': 'readable', 'scope': 'entry_only'}
            if self.access and self.access.described:
                result['display_path'] = self.access.display(candidate)
            return result
        finally:
            os.close(fd)

    def browse(self, path='', offset=0):
        if not isinstance(path, str) or not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset < self.max_entries:
            raise ValueError('폴더 경로와 목록 위치를 확인해주세요.')
        candidate = self._path(path, default_home=True)
        # Walk using descriptors: neither the requested directory nor any parent
        # may be a symlink, including during a concurrent filesystem change.
        fd = os.open(candidate.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in candidate.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            entries = []
            with os.scandir(fd) as iterator:
                for count, entry in enumerate(iterator, 1):
                    if count > self.max_entries:
                        raise ValueError('항목이 너무 많습니다. 하위 폴더의 전체 경로를 입력해주세요.')
                    target = candidate / entry.name
                    if entry.name.startswith('.') or self._private(target) or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        kind = 'directory'
                    elif entry.is_file(follow_symlinks=False):
                        kind = 'file'
                    else:
                        continue
                    entries.append({'name': entry.name, 'path': str(target), 'kind': kind})
        finally:
            os.close(fd)
        entries.sort(key=lambda entry: (entry['kind'] != 'directory', entry['name'].casefold(), entry['name']))
        has_more = len(entries) > offset + self.limit
        parent = candidate.parent
        if self.access and self.access.restricted and candidate.resolve() == self.access.root():
            parent = candidate
        result = {'path': str(candidate), 'parent': str(parent) if parent != candidate and not self._private(parent) else None,
                'entries': entries[offset:offset + self.limit], 'has_more': has_more,
                'next_offset': offset + self.limit if has_more else None}
        if self.access and self.access.described:
            result['display_path'] = self.access.display(candidate)
            for entry in result['entries']:
                entry['display_path'] = self.access.display(entry['path'])
        return result
