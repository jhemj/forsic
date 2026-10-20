"""Ephemeral Telegram activity, never a message, investigation or delivery retry."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import sqlite3
import time

from forsic_plugin.investigations import readonly


def case_is_working(home, item, *, now=None):
    """Observe a native live turn AND its case activity, not a persisted goal flag.

    Expiry/liveness only controls a five-second display. This does not reclaim a
    lease, retry a request or decide whether an investigation may resume.
    """
    from hermes_state import SessionDB, _compression_lock_holder_process_is_dead
    sessions = item.get('sessions') or []
    if not sessions:
        return False
    now = time.time() if now is None else now
    native = None
    try:
        native = SessionDB(db_path=Path(home) / 'state.db', read_only=True)
        roots = {sid: native._session_turn_lease_key(sid) for sid in sessions}
        keys = tuple(set(roots.values()))
        marks = ','.join('?' for _ in keys)
        with readonly(Path(home) / 'state.db') as db:
            leases = db.execute(f'SELECT conversation_id,holder,acquired_at FROM session_turn_leases '
                                f'WHERE conversation_id IN ({marks}) AND expires_at>?', (*keys, now)).fetchall()
        activity = Path(item['output_root']) / 'activity.sqlite3'
        if not activity.is_file():
            return False
        with readonly(activity) as db:
            for lease in leases:
                if _compression_lock_holder_process_is_dead(lease['holder']):
                    continue
                scoped = tuple(sid for sid, root in roots.items() if root == lease['conversation_id'])
                marks = ','.join('?' for _ in scoped)
                row = db.execute(f'SELECT kind FROM events WHERE session IN ({marks}) AND time>=? '
                    "AND kind IN ('turn_start','turn_complete','pre_api_request','post_api_request',"
                    "'api_request_error','tool_start','tool_result','pre_auxiliary_call','post_auxiliary_call') "
                    'ORDER BY time DESC,rowid DESC LIMIT 1', (*scoped, lease['acquired_at'])).fetchone()
                if row and row['kind'] not in ('turn_complete', 'api_request_error'):
                    return True
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error):
        return False  # An optional activity display must never block conversation.
    finally:
        if native is not None:
            native.close()
    return False


def working_topics(router):
    """Only already bound ready case topics are eligible; no route creation here."""
    topics = set()
    for item in router.catalog.list():
        route = router.state['routes'].get(item['case_id'], {})
        if (route.get('status') == 'ready' and route.get('thread_id') is not None
                and route.get('session') == item['session_id']
                and case_is_working(router.home, item)):
            topics.add(route['thread_id'])
    return topics


class ChatActions:
    """One four-second refresh loop; actual uploads take precedence over typing."""
    def __init__(self, bot, chat_id, active_topics, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.bot, self.chat_id, self.active_topics = bot, chat_id, active_topics
        self.clock, self.sleep = clock, sleep
        self.uploads, self.sent = {}, {}
        self.task = None
        self.lock = asyncio.Lock()
        self.last_error_type = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop())
        return self

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        self.uploads.clear()
        self.sent.clear()
        # Telegram has no "stop typing" request; the last action expires within
        # five seconds, or is cleared by the bot's next message.

    async def _send(self, topic, action):
        async with self.lock:
            if self.uploads.get(topic):
                action = 'upload_document'
            previous = self.sent.get(topic)
            now = self.clock()
            if previous and previous[0] == action and now - previous[1] < 4:
                return
            self.sent[topic] = (action, now)
            try:
                await asyncio.wait_for(self.bot.send_chat_action(chat_id=self.chat_id,
                    message_thread_id=topic, action=action, connect_timeout=2,
                    read_timeout=2, write_timeout=2, pool_timeout=2), timeout=2)
            except Exception as exc:
                self.last_error_type = type(exc).__name__  # Never retain token URLs/body.

    async def tick(self):
        try:
            topics = set(self.active_topics())
        except Exception as exc:
            self.last_error_type = type(exc).__name__
            topics = set()
        topics.update(self.uploads)
        for topic in topics:
            await self._send(topic, 'upload_document' if self.uploads.get(topic) else 'typing')
        for topic in set(self.sent) - topics:
            self.sent.pop(topic, None)

    async def _loop(self):
        while True:
            began = self.clock()
            await self.tick()
            await self.sleep(max(0, 4 - (self.clock() - began)))

    @asynccontextmanager
    async def upload_document(self, topic):
        self.uploads[topic] = self.uploads.get(topic, 0) + 1
        try:
            await self._send(topic, 'upload_document')
            yield
        finally:
            self.uploads[topic] -= 1
            if not self.uploads[topic]:
                self.uploads.pop(topic)
