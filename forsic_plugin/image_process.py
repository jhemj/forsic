"""One foreground parser process, native cancellation, and verified cleanup boundaries."""
import base64
import csv
import io
import json
import os
import selectors
import subprocess
import time
from .execution_contract import ExecutionFailure

IMAGE_TIMEOUT_SECONDS = 300
CLEANUP_TIMEOUT_SECONDS = 15


def bind_mount(source, destination, readonly, *, recursive_readonly=False):
    """Encode one Docker --mount CSV record; paths never become mount options."""
    source, destination = str(source), str(destination)
    if any(char in source + destination for char in ('\0', '\n', '\r')):
        raise ValueError('Docker bind paths must not contain NUL or newlines')
    fields = ['type=bind', 'src=' + source, 'dst=' + destination]
    if readonly:
        fields.append('readonly')
        if recursive_readonly:
            fields.extend(('bind-propagation=rprivate', 'bind-recursive=readonly'))
    stream = io.StringIO()
    csv.writer(stream, lineterminator='').writerow(fields)
    return stream.getvalue()


def _cleanup(container, process):
    """Removal success confirms this named container stopped; errors preserve uncertainty."""
    result = {'requested': True, 'confirmed': False}
    # Detach/reap the client first: an output-limited attach stream otherwise
    # backpressures the daemon while docker rm waits for container termination.
    # Client exit alone never confirms that the container stopped.
    try:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
        result['client_reaped'] = True
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(client_reaped=False, client_error_type=type(exc).__name__)
    try:
        removed = subprocess.run(['docker', 'rm', '-f', container], capture_output=True,
                                 timeout=CLEANUP_TIMEOUT_SECONDS, check=False)
        result.update(exit_code=removed.returncode,
                      stderr=removed.stderr.decode(errors='replace')[-2000:])
        result['confirmed'] = removed.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        result['error_type'] = type(exc).__name__
    for stream in (getattr(process,'stdout',None), getattr(process,'stderr',None)):
        if hasattr(stream,'close'):
            stream.close()
    return result


def _raw_output(stdout, stderr, *, complete, limit):
    return {'stdout_base64':base64.b64encode(stdout).decode(),
            'stderr_base64':base64.b64encode(stderr).decode(),
            'stdout_bytes':len(stdout), 'stderr_bytes':len(stderr),
            'retained_bytes':len(stdout)+len(stderr), 'complete':complete,
            'truncated':not complete, 'limit_bytes':limit}


class _BoundedCapture:
    """Drain both pipes without retaining more than the combined byte ceiling."""
    def __init__(self, process, limit):
        self.selector = selectors.DefaultSelector()
        self.buffers = [bytearray(), bytearray()]
        self.limit, self.exceeded = limit, False
        for index, stream in enumerate((process.stdout, process.stderr)):
            self.selector.register(stream, selectors.EVENT_READ, index)

    def step(self, timeout):
        for key, _ in self.selector.select(timeout):
            block = os.read(key.fileobj.fileno(), 65536)
            if not block:
                self.selector.unregister(key.fileobj)
                continue
            available = self.limit - sum(map(len, self.buffers))
            self.buffers[key.data].extend(block[:available])
            if len(block) > available:
                self.exceeded = True
                break
        return not self.selector.get_map()

    def output(self):
        return tuple(bytes(value) for value in self.buffers)

    def close(self):
        self.selector.close()


def run_container(command, container, native_execution=None, *, timeout_seconds=None,
                  max_output_bytes=None, cleanup_on_return=False, process_observer=None):
    """One native foreground attempt; raw bytes never assert execution metadata.

    Parsers keep their historical communicate/JSON contract. Arbitrary commands use
    bounded pipe draining and explicit container removal even after ordinary exit.
    """
    parser = max_output_bytes is None
    prefix = 'parser' if parser else 'command'
    if native_execution and native_execution.interrupted():
        raise ExecutionFailure('Native cancellation was requested before parser launch.',
            error_code='cancelled_before_start', failure_stage='admission',
            execution={'state':'not_started', 'backend':'docker', 'termination_confirmed':True})
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **({} if parser else {'stdin':subprocess.DEVNULL}))
    except OSError as exc:
        raise ExecutionFailure(str(exc), error_code='backend_unavailable', error_type=type(exc).__name__,
            failure_stage='launch', execution={'state':'not_started', 'backend':'docker',
                'termination_confirmed':True}) from exc
    started = time.monotonic()
    capture, stdout, stderr = None, b'', b''
    try:
        if process_observer is not None:
            process_observer(process.pid)
        if not parser:
            capture = _BoundedCapture(process, max_output_bytes)
        while True:
            cancelled = bool(native_execution and native_execution.interrupted())
            remaining = (IMAGE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds) - (time.monotonic() - started)
            exceeded = bool(capture and capture.exceeded)
            if cancelled or remaining <= 0 or exceeded:
                if capture:
                    stdout, stderr = capture.output()
                cleanup = _cleanup(container, process)
                state = ('cancelled' if cancelled else 'failed') if cleanup['confirmed'] else 'completion_unknown'
                raise ExecutionFailure('Native cancellation requested.' if cancelled else
                    'Command output exceeded its byte limit.' if exceeded else
                    'Read-only image parser timed out.' if parser else 'Container command timed out.',
                    error_code='cancel_requested' if cancelled else 'command_output_limit' if exceeded else prefix+'_timeout',
                    error_type='NativeInterrupt' if cancelled else 'OutputLimitExceeded' if exceeded else 'TimeoutExpired', failure_stage='execution',
                    **({} if parser else {'raw_output':_raw_output(stdout,stderr,complete=False,limit=max_output_bytes)}),
                    execution={'state':state, 'backend':'docker', 'container':container,
                        'termination_confirmed':cleanup['confirmed'], 'cleanup':cleanup})
            try:
                if capture:
                    if capture.step(min(.1, remaining)) and process.poll() is not None:
                        stdout, stderr = capture.output()
                        break
                    continue
                stdout, stderr = process.communicate(timeout=min(1, remaining) if native_execution else remaining)
                break
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt as exc:
        if capture:
            stdout,stderr=capture.output()
            exc.forsic_raw_output=_raw_output(stdout,stderr,complete=False,limit=max_output_bytes)
        exc.forsic_execution = {'state':'cancelled', 'backend':'docker', 'container':container,
                                'cleanup':_cleanup(container, process)}
        exc.forsic_execution['termination_confirmed'] = exc.forsic_execution['cleanup']['confirmed']
        if not exc.forsic_execution['termination_confirmed']:
            exc.forsic_execution['state'] = 'completion_unknown'
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        if capture:
            stdout, stderr = capture.output()
        cleanup = _cleanup(container, process)
        raise ExecutionFailure('Parser communication failed; the cleanup result is recorded separately.',
            error_code=prefix+'_communication_failed', error_type=type(exc).__name__, failure_stage='execution',
            **({} if parser else {'raw_output':_raw_output(stdout,stderr,complete=False,limit=max_output_bytes)}),
            execution={'state':'failed' if cleanup['confirmed'] else 'completion_unknown',
                'backend':'docker', 'container':container, 'termination_confirmed':cleanup['confirmed'],
                'cleanup':cleanup}) from exc
    finally:
        if capture:
            capture.close()
    if parser and process.returncode:
        cleanup = _cleanup(container, process)
        raise ExecutionFailure('Read-only image parser process failed.', error_code='parser_process_failed',
            error_type='ProcessError', failure_stage='execution', stderr=stderr.decode(errors='replace')[-2000:],
            execution={'state':'failed' if cleanup['confirmed'] else 'completion_unknown', 'backend':'docker',
                'container':container, 'exit_code':process.returncode,
                'termination_confirmed':cleanup['confirmed'], 'cleanup':cleanup})
    execution = {'state':'returned', 'backend':'docker', 'container':container,
                 'exit_code':process.returncode, 'termination_confirmed':True}
    if cleanup_on_return:
        cleanup = _cleanup(container, process)
        execution.update(cleanup=cleanup, termination_confirmed=cleanup['confirmed'])
        if not cleanup['confirmed']:
            execution['state']='completion_unknown'
    if not parser:
        process.stdout.close()
        process.stderr.close()
    return {'stdout':stdout, 'stderr':stderr, 'execution':execution}


def run_parser(command, container, native_execution=None):
    result = run_container(command, container, native_execution)
    stdout, stderr, execution = result['stdout'], result['stderr'], result['execution']
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError('Parser output must be a JSON object')
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExecutionFailure('The parser returned malformed output.', error_code='parser_output_invalid',
            error_type=type(exc).__name__, failure_stage='decode', execution={**execution,'state':'failed'},
            raw_output={'base64':base64.b64encode(stdout).decode(), 'bytes':len(stdout)},
            stderr=stderr.decode(errors='replace')[-2000:]) from exc
    if data.get('error'):
        code = {'FileNotFoundError':'not_found', 'PermissionError':'permission_denied'}.get(data.get('error_type'), 'parser_reported_error')
        data = {**data, 'failure_stage':'parser', 'error_code':code}
        execution['state'] = 'failed'
    # The parser cannot assert trusted execution/cleanup or retry eligibility.
    for key in ('execution', 'retryable_read_failure', 'retry_call', 'started_id', 'evidence_id',
                'mission_context', 'argument_resolution', 'execution_rejected', 'backend_started', 'tool'):
        data.pop(key, None)
    return {**data, 'execution':execution}
