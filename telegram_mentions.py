"""Opt-in group mentions; only the mirror's assistant bot consumes Telegram updates."""
import hashlib
import json
from pathlib import Path
import time
import unicodedata

from forsic_plugin.intake import write_json
from forsic_plugin.telegram_bridge import admit


def mention_message(update, bot_id, username, chat_id, since):
    """Telegram entity offsets are UTF-16 code units, not Python character indices."""
    msg = update.get('message')
    if not isinstance(msg, dict) or update.get('edited_message'):
        return None
    sender, chat = msg.get('from') or {}, msg.get('chat') or {}
    if (sender.get('is_bot') or not isinstance(sender.get('id'), int) or msg.get('sender_chat')
            or chat.get('type') not in ('group', 'supergroup') or str(chat.get('id')) != str(chat_id)
            or float(msg.get('date') or 0) < since or not msg.get('message_thread_id')):
        return None
    text = msg.get('text')
    if not isinstance(text, str) or len(text) > 6000:
        return None
    raw = text.encode('utf-16-le')
    spans = []
    for entity in msg.get('entities') or []:
        offset, length = entity.get('offset'), entity.get('length')
        if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length <= 0:
            continue
        if (offset + length) * 2 > len(raw):
            continue
        try:
            label = raw[offset * 2:(offset + length) * 2].decode('utf-16-le')
        except UnicodeDecodeError:
            continue
        tagged = (entity.get('type') == 'mention' and label.casefold() == ('@' + username).casefold())
        tagged |= (entity.get('type') == 'text_mention' and (entity.get('user') or {}).get('id') == bot_id)
        if tagged:
            spans.append((offset * 2, (offset + length) * 2))
    if not spans:
        return None
    for start, end in sorted(spans, reverse=True):
        raw = raw[:start] + raw[end:]
    body = raw.decode('utf-16-le').strip()
    if not body or body.startswith(('/', '!')) or any(unicodedata.category(ch) == 'Cc' and ch not in '\n\t' for ch in body):
        return None
    name = ' '.join(str(sender.get(k) or '') for k in ('first_name', 'last_name')).strip()
    handle = str(sender.get('username') or '')
    name = ''.join(ch for ch in (name or handle or str(sender['id'])) if unicodedata.category(ch) not in ('Cc','Cf'))
    name = ' '.join(name.split())[:120] or str(sender['id'])
    return dict(body=body, name=name, sender_id=sender['id'], message_id=msg['message_id'],
                thread_id=msg['message_thread_id'], update_id=update['update_id'])


class Mentions:
    def __init__(self, home, intake, path, chat_id, bot_id, username, routes):
        self.home, self.intake, self.path = Path(home), Path(intake), Path(path)
        self.chat_id, self.bot_id, self.username, self.routes = str(chat_id), bot_id, username, routes
        self.state = json.loads(self.path.read_text()) if self.path.exists() else dict(
            chat_id=self.chat_id, bot_id=bot_id, since=time.time(), offset=0, requests={})
        if self.state['chat_id'] != self.chat_id or self.state['bot_id'] != bot_id:
            raise ValueError('Mention inbox belongs to another bot or group')
        self.save()

    def save(self):
        write_json(self.path, self.state)
        self.path.chmod(0o600)

    def receive(self, updates):
        for update in sorted(updates, key=lambda item: item['update_id']):
            if update['update_id'] < self.state['offset']:
                continue
            msg = mention_message(update, self.bot_id, self.username, self.chat_id, self.state['since'])
            if msg:
                matched = [(cid, route) for cid, route in self.routes().items()
                           if route.get('status') == 'ready' and route.get('thread_id') == msg['thread_id']]
                if len(matched) == 1:
                    cid, route = matched[0]
                    key = hashlib.sha256(f"telegram:{self.bot_id}:{self.chat_id}:{msg['message_id']}".encode()).hexdigest()
                    text = f"[Telegram · {msg['name']} · #{msg['message_id']}]\n{msg['body']}"
                    self.state['requests'].setdefault(key, dict(delivery_id=key, case_id=cid,
                        session_id=route['session'], thread_id=msg['thread_id'], message_id=msg['message_id'],
                        text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(), status='waiting_session',
                        author={'id':f"telegram:{msg['sender_id']}", 'name':msg['name'], 'is_bot':False}))
            self.state['offset'] = update['update_id'] + 1
            self.save()  # Durable admission before acknowledging the next getUpdates offset.

    def dispatch(self, deliver=admit):
        for request in self.state['requests'].values():
            if request['status'] not in ('waiting_session', 'queued', 'claimed'):
                continue
            try:
                result = deliver(self.home, self.intake, request)
            except Exception as exc:
                # Admission is idempotent by deterministic ID. Inspect the same
                # native receipt next tick; never mint a replacement delivery.
                request['error_type'] = type(exc).__name__
            else:
                request.update(result)
                request.pop('error_type', None)
            self.save()

    async def tick(self, bot):
        updates = await bot.get_updates(offset=self.state['offset'], limit=100, timeout=0,
                                        allowed_updates=['message'])
        self.receive([item.to_dict() for item in updates])
        self.dispatch()
