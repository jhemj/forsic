"""Session identity shared by schema callbacks and native dispatch, without domain policy."""
from contextlib import contextmanager
from contextvars import ContextVar

_current_session = ContextVar('native_tool_schema_session', default='')


def current_session_id():
    return _current_session.get()


@contextmanager
def tool_schema_scope(session_id):
    token = _current_session.set(session_id or '')
    try:
        yield
    finally:
        _current_session.reset(token)
