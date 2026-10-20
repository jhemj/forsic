"""Opaque, profile-owned transfer tokens and bounded idempotent UI requests."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

TOKEN = re.compile(r'[a-f0-9]{32}\Z')
LIMITS = {'pack': 32 * 1024**2, 'source': 32 * 1024**2, 'environment': 32 * 1024**3}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ManagementStore:
    def __init__(self, home, *, root=None):
        from forsic_plugin.tenants import current_scope
        scope=current_scope()
        if scope is not None and Path(home).resolve()!=Path(scope['profile_home']).resolve():
            raise PermissionError('관리 저장소가 선택한 테넌트의 프로필과 다릅니다.')
        expected=Path(scope['management_root'])/'ui' if scope is not None else Path(home)/'forsic-management'/'ui'
        if root is not None and Path(root).resolve()!=expected.resolve():
            raise PermissionError('관리 파일 저장 위치가 테넌트 소유 범위와 다릅니다.')
        self.root = expected
        self.tenant_id = scope['tenant_id'] if scope is not None else None

    def _scope_check(self):
        from forsic_plugin.tenants import current_scope
        scope=current_scope()
        if (scope['tenant_id'] if scope is not None else None)!=self.tenant_id:
            raise PermissionError('관리 요청 중 테넌트 소유 범위가 변경되었습니다.')
        if self.root.is_symlink():raise PermissionError('관리 저장소 링크는 허용하지 않습니다.')

    def write(self, path, value):
        self._scope_check()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name('.' + uuid.uuid4().hex)
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @contextmanager
    def lock(self):
        self._scope_check()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self, path, default=None):
        self._scope_check()
        try:
            if path.is_symlink(): raise ValueError('관리 기록의 링크는 허용하지 않습니다.')
            return json.loads(path.read_text())
        except FileNotFoundError:
            return default

    def operation(self, operation_id):
        return self.read(self.root / 'requests' / (digest(operation_id) + '.json'))

    def save_operation(self, operation_id, fingerprint, response):
        self.write(self.root / 'requests' / (digest(operation_id) + '.json'),
                   {'fingerprint': fingerprint, 'response': response})

    def transfer_path(self, token, family='uploads'):
        self._scope_check()
        if family not in ('uploads','artifacts'):raise ValueError('잘못된 관리 파일 종류입니다.')
        if not TOKEN.fullmatch(token): raise ValueError('잘못된 파일 식별자입니다.')
        return self.root / family / token

    async def upload(self, uploaded, kind):
        if kind not in LIMITS: raise ValueError('지원하지 않는 파일 종류입니다.')
        token = uuid.uuid4().hex
        path = self.transfer_path(token)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        size, checksum = 0, hashlib.sha256()
        try:
            with path.open('xb') as stream:
                os.chmod(path, 0o600)
                while chunk := await uploaded.read(1024 * 1024):
                    size += len(chunk)
                    if size > LIMITS[kind]: raise OverflowError('가져오기 허용 크기를 넘었습니다.')
                    checksum.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not size: raise ValueError('빈 파일은 가져올 수 없습니다.')
            path.chmod(0o400)
            metadata = {'upload_id': token, 'sha256': checksum.hexdigest(), 'bytes': size,
                        'name': Path(uploaded.filename or 'archive').name[:200], 'kind': kind}
            self.write(path.with_suffix('.json'), metadata)
            return metadata
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def upload_path(self, token, kind):
        path = self.transfer_path(token)
        metadata = self.read(path.with_suffix('.json'))
        if not metadata or metadata.get('kind') != kind: raise ValueError('가져올 파일 종류가 일치하지 않습니다.')
        self.verify(path, metadata, LIMITS[kind])
        return path

    @staticmethod
    def verify(path, metadata, maximum):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != metadata['bytes'] or info.st_size > maximum:
                raise ValueError('관리 파일의 크기나 종류가 변경되었습니다.')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['sha256']:
                    raise ValueError('관리 파일 내용이 변경되었습니다.')
        finally:
            os.close(fd)

    def artifact_destination(self, token):
        path = self.transfer_path(token, 'artifacts')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def register_artifact(self, token):
        path = self.artifact_destination(token)
        if path.is_symlink() or not path.is_file(): raise ValueError('완성된 내보내기 파일이 없습니다.')
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        metadata = {'artifact_id': token, 'sha256': checksum, 'bytes': path.stat().st_size}
        if metadata['bytes'] > LIMITS['environment']: raise OverflowError('내보내기 허용 크기를 넘었습니다.')
        path.chmod(0o400)
        self.write(path.with_suffix('.json'), metadata)
        return metadata

    def artifact(self, token):
        path = self.transfer_path(token, 'artifacts')
        metadata = self.read(path.with_suffix('.json'))
        if not metadata: raise FileNotFoundError('내보낸 파일이 아직 준비되지 않았습니다.')
        self.verify(path, metadata, LIMITS['environment'])
        return path, metadata
