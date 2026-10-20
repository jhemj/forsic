"""One bounded native auxiliary request per goal; receipts remain authoritative."""
from dataclasses import dataclass, field
import logging
import threading
import time

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_jobs = {}


@dataclass
class PendingReview:
    request_id: str
    session_ids: tuple
    native_db: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: object = None
    deadline: float = field(default_factory=lambda: time.monotonic()+600)


def start(key, *, request_id, session_ids, native_db, lease, complete, cancel_check, still_bound):
    """Transfer an already recorded request/lease to the native context-bound thread.

    No queued work or retry exists here. The bounded auxiliary stream owns its
    deadline; native session and plugin teardown only latch cancellation.
    """
    from agent.memory_provider import spawn_context_thread
    job = PendingReview(request_id, tuple(session_ids), str(native_db))

    def cancelled():
        if job.cancel.is_set():
            return True
        try:
            if (callable(cancel_check) and cancel_check()) or not still_bound():
                job.cancel.set()
        except Exception:
            job.cancel.set()
        return job.cancel.is_set()

    def run():
        try:
            with lease:
                complete(cancelled,job.deadline)
        except Exception as exc:
            # An unfinished request is retained as unknown, never scheduled again.
            logger.warning('Independent review worker ended without a settled receipt (%s)', type(exc).__name__)
        finally:
            with _lock:
                if _jobs.get(key) is job:
                    del _jobs[key]
            job.done.set()

    with _lock:
        if key in _jobs or any(job.native_db==str(native_db) and set(job.session_ids).intersection(session_ids)
                                for job in _jobs.values()):
            lease.close()
            raise ValueError('Independent review is already running for this goal')
        job.thread = spawn_context_thread(run, name='forsic-independent-review')
        _jobs[key] = job
        try:
            job.thread.start()
        except BaseException:
            del _jobs[key]
            lease.close()
            job.done.set()
            raise
    return job


def inflight(key):
    with _lock:
        job = _jobs.get(key)
        return job.request_id if job and not job.done.is_set() else None


def cancel_scope(*, native_db, session_id=None):
    """Lifecycle notification, never a join or another worker."""
    with _lock:
        for job in _jobs.values():
            if job.native_db == str(native_db) and (session_id is None or session_id in job.session_ids):
                job.cancel.set()


def wait_existing(key, *, cancel_check=None):
    """Final boundary only: collect the existing request within its original deadline."""
    with _lock:
        job = _jobs.get(key)
    if job is None:return None
    while not job.done.is_set():
        if callable(cancel_check) and cancel_check():
            job.cancel.set()
            return 'cancelled'
        remaining=job.deadline-time.monotonic()
        if remaining<=0:
            job.cancel.set()
            return 'in_progress'
        job.done.wait(min(0.1,remaining))
    return 'collected'


def pending_for(key, *, native_db, session_ids):
    """A replaced goal cannot overlap another request in the same native session."""
    with _lock:
        for previous,job in _jobs.items():
            if job.native_db==str(native_db) and set(job.session_ids).intersection(session_ids):
                if previous!=key:job.cancel.set()
                return previous,job.request_id
    return None
