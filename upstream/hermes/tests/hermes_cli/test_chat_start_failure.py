"""A failed frontend build is a terminal startup failure, not a dropped socket."""
import asyncio
import subprocess
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize('failure', [
    subprocess.CalledProcessError(1, ['node', 'build', 'private-argument']),
    subprocess.TimeoutExpired(['node', 'build', 'private-argument'], 10),
    FileNotFoundError('private-build-path'),
])
def test_pre_spawn_failure_closes_with_actionable_message_without_launch(monkeypatch, failure):
    from hermes_cli.web_routers import chat_ws
    from hermes_cli import web_server_chat

    ws = AsyncMock()
    ws.query_params = {'resume': 'existing-conversation'}
    monkeypatch.setattr(chat_ws, '_ws_gate', AsyncMock(return_value=('local', 'test', 'token')))
    monkeypatch.setattr(web_server_chat, '_PTY_BRIDGE_AVAILABLE', True)
    monkeypatch.setattr(chat_ws, '_resolve_chat_argv_async', AsyncMock(side_effect=failure))
    spawn = AsyncMock(side_effect=AssertionError('must not launch a replacement session'))
    monkeypatch.setattr(web_server_chat.PtyBridge, 'spawn', spawn)
    asyncio.run(chat_ws.pty_ws(ws))
    ws.accept.assert_awaited_once()
    ws.close.assert_awaited_once_with(code=1011)
    message = ws.send_text.call_args.args[0]
    assert 'Chat could not start' in message
    assert 'build' in message.lower() or 'server log' in message.lower()
    assert 'private-' not in message
    spawn.assert_not_called()


def test_cancelled_start_is_not_reclassified_as_a_build_failure(monkeypatch):
    from hermes_cli.web_routers import chat_ws
    from hermes_cli import web_server_chat

    ws = AsyncMock()
    ws.query_params = {'resume': 'existing-conversation'}
    monkeypatch.setattr(chat_ws, '_ws_gate', AsyncMock(return_value=('local', 'test', 'token')))
    monkeypatch.setattr(web_server_chat, '_PTY_BRIDGE_AVAILABLE', True)
    monkeypatch.setattr(chat_ws, '_resolve_chat_argv_async', AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(chat_ws.pty_ws(ws))
    ws.close.assert_not_awaited()
