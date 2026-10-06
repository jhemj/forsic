"""One durable background ewfverify per unchanged segment set, not an agent loop."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .intake import write_json


def segment_state(case, path):
    # libewf opens numbered siblings itself; validate the same selected set first.
    siblings = sorted(p for p in path.parent.iterdir()
                      if p.stem == path.stem and p.suffix.lower().startswith('.e'))
    return [{'path': str(case.path(str(p))), 'bytes': p.stat().st_size,
             'mtime_ns': p.stat().st_mtime_ns} for p in siblings]


def verify(case, path, action='start'):
    if action not in ('start', 'status'):
        raise ValueError('Verification action must be start or status')
    identity = {'path': str(path), 'segments': segment_state(case, path),
                'timeout_seconds': max(1, int(case.config.get('verify_timeout_seconds', 21600)))}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    target = case.output / ('verify-' + key + '.json')
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            stored = json.loads(target.read_text())
            if stored.get('status') == 'running' and stored.get('pid'):
                try:
                    os.kill(stored['pid'], 0)  # Liveness query only, not a signal or restart.
                except ProcessLookupError:
                    stored.update(status='worker_unavailable', verification_complete=False)
            return {**stored, 'reused': True}
        if action == 'status':
            return {'status': 'not_started', 'path': str(path), 'verification_complete': False}
        job = {**identity, 'job_id': key, 'status': 'queued', 'started_at': time.time(),
               'verification_complete': False, 'receipt': str(target)}
        write_json(target, job)
        try:
            subprocess.Popen([sys.executable, '-m', 'forsic_plugin.verification', str(target)],
                             cwd=Path(__file__).resolve().parents[1], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            job.update(status='not_started', error_type=type(exc).__name__)
            write_json(target, job)
        return job


def run(target):
    target = Path(target)
    # The worker lock also protects against an accidental duplicate launch.
    with target.with_suffix('.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job = json.loads(target.read_text())
        if job['status'] != 'queued':
            return
        job.update(status='running', pid=os.getpid())
        write_json(target, job)
        stdout, stderr = target.with_suffix('.stdout'), target.with_suffix('.stderr')
        try:
            with stdout.open('wb') as out, stderr.open('wb') as err:
                result = subprocess.run(['ewfverify', job['path']], stdout=out, stderr=err,
                                        timeout=job['timeout_seconds'], check=False)
            unchanged = all(Path(s['path']).stat().st_size == s['bytes'] and
                            Path(s['path']).stat().st_mtime_ns == s['mtime_ns'] for s in job['segments'])
            success = result.returncode == 0 and unchanged
            job.update(status='completed' if success else 'failed', exit_code=result.returncode,
                       verification_complete=success, segment_metadata_unchanged=unchanged)
        except subprocess.TimeoutExpired:
            job.update(status='timed_out', error_type='TimeoutExpired')
        except OSError as exc:
            job.update(status='failed', error_type=type(exc).__name__)
        for name, path in [('stdout', stdout), ('stderr', stderr)]:
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 12000))
                    job[name] = stream.read().decode(errors='replace')
                job[name + '_file'] = str(path)
        job.update(finished_at=time.time(), meaning='Use the ewfverify result and stored-hash availability; completion is not proof of acquisition provenance.')
        write_json(target, job)


if __name__ == '__main__':
    run(sys.argv[1])
