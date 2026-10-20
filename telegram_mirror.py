"""Important case notifications with existing mentions/report delivery. No agent loop."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from forsic_plugin.intake import write_json
from forsic_plugin.investigations import Investigations, readonly

POLICY = 'important-only-v1'


def record_clarification(case, phase, tool_name, args=None, result=None, tool_call_id='', session_id='', **kwargs):
    """Observe the native user-input tool; investigation notes are not requests.

    This hook never approves, changes a tool call, answers a form or starts work.
    The terminal observer resolves even blocked/cancelled requests.
    """
    if tool_name != 'clarify' or phase not in ('requested', 'resolved') or not tool_call_id or not session_id:
        return
    native = {key: kwargs.get(key, '') for key in ('turn_id', 'api_request_id')}
    native.update(tool_call_id=tool_call_id, session_id=session_id)
    identity = hashlib.sha256(json.dumps(native, sort_keys=True).encode()).hexdigest()
    value = {'request_id': identity, 'native_call': native}
    if phase == 'requested':
        from tools.clarify_tool import _normalize_questions
        questions, error = _normalize_questions((args or {}).get('questions'))
        if error: return
        value['questions'] = [{key: item[key] for key in ('question', 'choices')} for item in questions]
    case.record('user_input_' + phase, value, session_id)


def important_judgment(current, previous):
    """Explicit adopted, material answer changes; never an internal question prompt."""
    investigation = current.get('investigation') or {}
    old = (previous or {}).get('investigation') or {}
    if (investigation.get('priority') not in ('decision_critical', 'material')
            or not investigation.get('claim_refs') or investigation.get('judgment_review_required')
            or not current.get('answer') or not current.get('correction_reason')):
        return ''
    if previous and current['answer'] == previous.get('answer') and investigation.get('assessment') == old.get('assessment'):
        return ''
    return '📌 중요한 판단 변경\n' + current['answer'] + '\n\n이유: ' + current['correction_reason']


def public_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content
                         if isinstance(x, dict) and x.get('type') == 'text').strip()
    return ''


def message_text(row):
    if row['role'] not in ('user', 'assistant') or row['display_kind']:
        return ''
    if row['_compressed_summary'] or not row['active'] or row['compacted']:
        return ''
    content = row['content'] or ''
    if content.startswith('['):
        try:
            content = json.loads(content)
        except ValueError:
            pass
    text = public_text(content)
    return text


def assistant_plain_text(text):
    """Render assistant Markdown as text without rewriting literal code or paths.

    Markdown-it is already provided by the native Hermes runtime. Parse syntax
    instead of removing punctuation: underscores, evidence IDs and fenced code
    are content, and link destinations must remain available in plain Telegram.
    """
    from markdown_it import MarkdownIt

    def inline(tokens):
        parts, links = [], []
        for token in tokens or []:
            if token.type == 'link_open':
                links.append((len(parts), token.attrGet('href') or ''))
            elif token.type == 'link_close':
                start, target = links.pop()
                label = ''.join(parts[start:])
                if target and target != label:
                    parts.append(' (' + target + ')')
            elif token.type == 'image':
                label, target = inline(token.children), token.attrGet('src') or ''
                parts.append(label)
                if target and target != label:
                    parts.append(' (' + target + ')')
            elif token.type in ('softbreak', 'hardbreak'):
                parts.append('\n')
            elif token.type in ('text', 'code_inline', 'html_inline'):
                parts.append(token.content)
            elif token.children:
                parts.append(inline(token.children))
        return ''.join(parts)

    parts, lists = [], []
    table_cell = 0

    def newline(count=1):
        if parts:
            # Add structural spacing without normalizing whitespace inside code.
            tail = parts[-1]
            existing = len(tail) - len(tail.rstrip('\n'))
            if existing < count:
                parts.append('\n' * (count - existing))

    parser = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])
    for token in parser.parse(text):
        kind = token.type
        if kind in ('bullet_list_open', 'ordered_list_open'):
            newline()
            lists.append(int(token.attrGet('start') or 1) if kind == 'ordered_list_open' else None)
        elif kind in ('bullet_list_close', 'ordered_list_close'):
            lists.pop()
            newline(1 if lists else 2)
        elif kind == 'list_item_open':
            newline()
            number = lists[-1]
            prefix = '• ' if number is None else str(number) + '. '
            parts.append('  ' * (len(lists) - 1) + prefix)
            if number is not None:
                lists[-1] += 1
        elif kind == 'list_item_close':
            newline()
        elif kind == 'inline':
            parts.append(inline(token.children))
        elif kind in ('paragraph_close', 'heading_close'):
            newline(1 if lists else 2)
        elif kind in ('fence', 'code_block'):
            newline()
            parts.append(token.content)
            newline(2)
        elif kind == 'tr_open':
            newline()
            table_cell = 0
        elif kind in ('th_open', 'td_open'):
            if table_cell:
                parts.append(' | ')
            table_cell += 1
        elif kind == 'tr_close':
            newline()
        elif kind in ('table_close', 'blockquote_close', 'hr'):
            newline(2)
    return ''.join(parts).strip('\n')


def progress_text(row):
    data = json.loads(row['data'])
    if row['kind'] == 'tool_start':
        args = data.get('arguments') or {}
        target = args.get('file_path') or data.get('path') or ''
        reason = str(data.get('reason') or '').strip()
        return '🔎 작업 중\n' + '\n'.join(x for x in (reason or str(data.get('tool', '확인')), str(target)) if x)
    if row['kind'] == 'tool_result':
        if data.get('error') or data.get('exit_code', 0) != 0:
            return '⚠️ 확인 실패\n' + str(data.get('tool', '작업')) + '\n' + str(data.get('error') or '도구가 오류로 종료됐어요.')[:600]
        if 'matches' in data and not data['matches']:
            return '🔎 검색 결과\n이번 검색 범위에서는 일치하는 기록을 찾지 못했어요.'
        return '✅ 작업 완료\n' + str(data.get('tool', '확인'))
    return ''


class Mirror:
    def __init__(self, home, intake, state_path, session, chat_id, case_id=''):
        self.home, self.intake = Path(home), Path(intake)
        self.path = Path(state_path)
        self.session, self.chat_id = session, str(chat_id)
        self.case_id = case_id
        self.user_message_keys = set()
        self.state = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.state and (self.state['session'] != session or self.state['chat_id'] != str(chat_id)):
            raise ValueError('기존 미러링의 대상과 다릅니다. 별도 상태 파일을 사용하세요.')

    def sessions(self):
        if self.case_id:
            item = Investigations(self.home, self.intake).get(case_id=self.case_id)
            if not item or self.session not in item['sessions']:
                raise ValueError('사건과 대표 대화 연결을 확인해주세요.')
            return item['sessions']
        with readonly(self.home / 'state.db') as db:
            rows = db.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM sessions WHERE id=? '
                              'UNION SELECT s.id FROM sessions s JOIN tree t ON s.parent_session_id=t.id) '
                              'SELECT id FROM tree', (self.session,)).fetchall()
        if not rows:
            raise ValueError('선택한 Hermes 대화가 없습니다.')
        return [r['id'] for r in rows]

    def activity(self, sessions):
        found = {}
        for session in sessions:
            binding = self.intake / (hashlib.sha256(session.encode()).hexdigest() + '.json')
            if not binding.exists():
                continue
            manifest = json.loads(binding.read_text()).get('manifest')
            if manifest:
                config = json.loads(Path(manifest).read_text())
                path = Path(config['output_root']) / 'activity.sqlite3'
                if path.exists():
                    found[str(path)] = path
        return found

    def initialize(self):
        """Begin now, never upload earlier conversation or earlier evidence results."""
        self.sessions()
        if self.state:
            return
        self.state = dict(session=self.session, chat_id=self.chat_id, since=time.time(),
                          delivered=[], pending=None, status='ready', notification_policy=POLICY,
                          important_since=time.time(), goal_states={})
        write_json(self.path, self.state)

    def _goals(self, db, sessions, *, baseline=False):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_meta'").fetchone(): return []
        known = self.state.setdefault('goal_states', {})
        items = []
        for sid in sessions:
            row = db.execute('SELECT value FROM state_meta WHERE key=?', ('goal:' + sid,)).fetchone()
            if not row: continue
            goal = json.loads(row['value'])
            identity = {'created_at': goal.get('created_at'), 'status': goal.get('status')}
            old = known.get(sid)
            same = old is not None and all(old.get(key) == value for key, value in identity.items())
            transition = (old or {}).get('transition', 0) + (0 if same or old is None else 1)
            known[sid] = {**identity, 'transition': transition}
            if baseline or same: continue
            # Newly discovered historical/compressed goal rows are baselines.
            if old is None and max(goal.get('last_turn_at') or 0, goal.get('created_at') or 0) < self.state['important_since']: continue
            status = goal.get('status')
            if status not in ('done', 'paused'): continue
            text = ('✅ 조사 목표 완료\n완료 판단과 보고서는 웹에서 확인해주세요.' if status == 'done' else
                    '⏸ 조사가 중단되었습니다.\n중단 사유와 재개 여부를 웹에서 확인해주세요.')
            key = 'goal:' + hashlib.sha256(json.dumps({'conversation': self.session, 'session': sid,
                **identity, 'transition': transition}, sort_keys=True).encode()).hexdigest()
            items.append((goal.get('last_turn_at') or time.time(), key, text))
        return items

    def batch(self):
        sessions = self.sessions()
        placeholders = ','.join('?' for _ in sessions)
        items = []
        # Policy cutover starts now. Preserve all old delivered/unknown receipts,
        # but never reinterpret the old transcript as an important-message backlog.
        migrated = self.state.get('notification_policy') != POLICY
        if migrated:
            self.state.update(notification_policy=POLICY, important_since=time.time(), goal_states={})
        with readonly(self.home / 'state.db') as db:
            items.extend(self._goals(db, sessions, baseline=migrated))
        for path in self.activity(sessions).values():
            with readonly(path) as db:
                rows = db.execute("SELECT rowid,id,time,kind,session,data FROM events WHERE time>=? "
                    "AND kind IN ('user_input_requested','note') ORDER BY rowid", (self.state['important_since'],)).fetchall()
                for row in rows:
                    data = json.loads(row['data'])
                    if row['kind'] == 'user_input_requested':
                        if row['session'] not in sessions: continue
                        resolved = db.execute("SELECT 1 FROM events WHERE kind='user_input_resolved' "
                            "AND json_extract(data,'$.request_id')=? LIMIT 1", (data['request_id'],)).fetchone()
                        if resolved: continue
                        questions = data.get('questions') or []
                        text = '\n\n'.join(q['question'] + ('\n' + '\n'.join('• ' + c for c in q['choices']) if q.get('choices') else '') for q in questions)
                        if text: items.append((row['time'], 'clarify:' + data['request_id'], '❓ 답변이 필요합니다\n' + text + '\n\n웹의 확인 요청에서 답해주세요.'))
                    else:
                        old = db.execute("SELECT data FROM events WHERE kind='note' AND rowid<? "
                            "AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1", (row['rowid'], data['note_id'])).fetchone()
                        text = important_judgment(data, json.loads(old['data']) if old else None)
                        if text: items.append((row['time'], 'judgment:' + row['id'], text))
        if migrated: write_json(self.path, self.state)
        delivered = set(self.state['delivered'])
        unique = {key: (stamp, key, text) for stamp, key, text in items}
        return [(key, text) for _, key, text in sorted(unique.values()) if text and key not in delivered]

    def isolate_progress_failure(self):
        pending = self.state.get('pending') or {}
        is_progress = pending.get('channel') == 'progress' or (
            pending.get('key', '').startswith('event:') and self.state.get('progress_message_id'))
        if is_progress and self.state.get('status') == 'delivery_unconfirmed':
            # Retain the uncertain attempt, but it need not block ordinary chat.
            self.state['progress_failure'] = {**pending, 'error_type': self.state.get('error_type'),
                                              'status': 'delivery_unconfirmed'}
            self.state.update(progress_paused=True, pending=None, status='ready')
            write_json(self.path, self.state)

    async def tick(self, send, update_progress=None, send_user=None):
        # The old mirror's unknown message attempt remains untouched. Important
        # notifications have a separate transport receipt, never a retry of it.
        delivery = self.state.setdefault('important_delivery', {'pending': None, 'status': 'ready'})
        if delivery['pending'] or delivery['status'] != 'ready':
            raise RuntimeError('중요 알림 전송 상태 확인이 필요합니다. 불확실한 전송은 자동 재전송하지 않습니다.')
        from gateway.platforms.base import BasePlatformAdapter, utf16_len
        from agent.redact import redact_sensitive_text
        batch = self.batch()
        for key, text in batch:
            text = assistant_plain_text(redact_sensitive_text(text, force=True))
            if not text.strip():
                self.state['delivered'].append(key)
                continue
            chunks = BasePlatformAdapter.truncate_message(text, 4000, len_fn=utf16_len)
            for index, chunk in enumerate(chunks):
                delivery['pending'] = {'key': key, 'chunk': index, 'began': time.time(), 'channel': 'important'}
                write_json(self.path, self.state)
                try:
                    receipt = await send(chunk)
                except Exception as exc:
                    delivery.update(status='delivery_unconfirmed', error_type=type(exc).__name__)
                    write_json(self.path, self.state)
                    raise RuntimeError('Telegram 중요 알림 전송 결과 미확인. 자동 재전송하지 않습니다.') from None
                self.state['last_message_id'] = receipt
                self.state['last_sender'] = 'assistant_bot'
            self.state['delivered'].append(key)
            delivery['pending'] = None
            write_json(self.path, self.state)
        # Persist active goal baselines even when there is nothing to send.
        write_json(self.path, self.state)


class TopicRouter:
    """One stable forum topic per case; Hermes remains the only session store."""
    def __init__(self, home, intake, path, chat_id, seed_session, legacy_path):
        self.home, self.intake, self.path = Path(home), Path(intake), Path(path)
        self.catalog = Investigations(home, intake)
        self.chat_id, self.seed_session, self.legacy_path = str(chat_id), seed_session, Path(legacy_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'chat_id': self.chat_id, 'since': time.time(), 'routes': {}}
        if self.state['chat_id'] != self.chat_id:
            raise ValueError('미러링 그룹이 기존 설정과 다릅니다.')
        write_json(self.path, self.state)

    async def tick(self, create_topic, send, update_progress=None, send_user=None, send_document=None):
        from agent.redact import redact_sensitive_text
        if send_document and 'reports_since' not in self.state:
            # Feature activation starts now; existing reports are not a backlog.
            self.state['reports_since'] = time.time()
            write_json(self.path, self.state)
        for item in self.catalog.list():
            cid = item['case_id']
            seed = self.seed_session in item['sessions']
            if cid not in self.state['routes'] and not seed and (item['created_at'] < self.state['since'] or item['synthetic']):
                continue  # No upload of other historical or synthetic investigations.
            route = self.state['routes'].setdefault(cid, {'session':item['session_id'], 'status':'new'})
            if route['status'] == 'topic_unconfirmed':
                continue  # An uncertain create must not silently create a duplicate topic.
            cursor = self.path.parent / 'telegram-cases' / (hashlib.sha256(cid.encode()).hexdigest() + '.json')
            # Cursor migration and binding failures are isolated per route too.
            # Never reset its cursor/unknown attempt to repair a changed identity.
            try:
                if not cursor.exists():
                    if seed and self.legacy_path.exists():
                        old = json.loads(self.legacy_path.read_text())
                        if old['session'] != item['session_id'] or old['chat_id'] != self.chat_id:
                            raise ValueError('이전 미러링 연결을 확인해주세요.')
                        write_json(cursor, old)  # Preserve delivered keys, timestamp and unknown state.
                    else:
                        write_json(cursor, dict(session=item['session_id'], chat_id=self.chat_id,
                            since=self.state['since'], delivered=[], pending=None, status='ready'))
                mirror = Mirror(self.home, self.intake, cursor, route['session'], self.chat_id, cid)
                mirror.initialize()
            except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                route['notification_delivery_status'] = 'binding_unavailable'
                route['error_type'] = type(exc).__name__
                write_json(self.path, self.state)
                continue
            if send_document and route['status'] == 'ready':
                from telegram_reports import ReportDelivery
                report_path = self.path.parent / 'telegram-reports' / (hashlib.sha256(cid.encode()).hexdigest() + '.json')
                try:
                    reports = ReportDelivery(report_path, item['output_root'], cid, self.chat_id,
                                             route['thread_id'], self.state['reports_since'], manifest=item['manifest'])
                    async def report_to_topic(name, payload, caption):
                        return await send_document(route['thread_id'], name, payload, caption)
                    route['report_delivery'] = await reports.tick(report_to_topic)
                    route.pop('report_error_type', None)
                except Exception as exc:
                    # Report failures must not stop conversation or mention delivery.
                    route['report_error_type'] = type(exc).__name__
                write_json(self.path, self.state)
            important = mirror.state.get('important_delivery') or {}
            if important.get('pending') or important.get('status', 'ready') != 'ready':
                route['notification_delivery_status'] = 'delivery_unconfirmed'
                write_json(self.path, self.state)
                continue
            if route['status'] == 'new':
                route['status'] = 'topic_unconfirmed'
                write_json(self.path, self.state)
                name = redact_sensitive_text(item['label'])[:90] + ' · ' + cid[-8:]
                try:
                    route['thread_id'] = await create_topic(name)
                except Exception as exc:
                    route['error_type'] = type(exc).__name__
                    write_json(self.path, self.state)
                    continue  # Keep this uncertain topic, without stopping other routes or mention polling.
                route['status'] = 'ready'
                write_json(self.path, self.state)
            async def to_topic(text):
                return await send(route['thread_id'], text)
            async def progress_to_topic(message_id, text):
                return await update_progress(route['thread_id'], message_id, text)
            async def user_to_topic(text):
                return await send_user(route['thread_id'], text)
            try:
                previous_message = mirror.state.get('last_message_id')
                await mirror.tick(to_topic, progress_to_topic if update_progress else None,
                                  user_to_topic if send_user else None)
            except RuntimeError:
                route['notification_delivery_status'] = 'delivery_unconfirmed'
            except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                route['notification_delivery_status'] = 'source_unavailable'
                route['error_type'] = type(exc).__name__
            else:
                route['notification_delivery_status'] = 'ready'
                if mirror.state.get('last_message_id') != previous_message:
                    route['last_message_id'] = mirror.state['last_message_id']
            write_json(self.path, self.state)


def group_target(home, explicit=None):
    """Explicit launch only; saved settings never retarget an already running mirror."""
    from forsic_plugin.connections import telegram_group, preferences
    if explicit is None:
        from hermes_cli.config import get_config_path, load_config_readonly
        if get_config_path().parent.resolve() != Path(home).resolve():
            raise ValueError('다른 프로필의 미러에는 --chat-id로 대상 그룹을 지정해주세요.')
        explicit = preferences(load_config_readonly()).get('telegram_group', '')
    target = telegram_group(explicit)
    if not target:
        raise ValueError('연결 설정에 그룹 ID·공개 주소를 저장하거나 --chat-id로 지정해주세요.')
    return int(target) if target.startswith('-') else target


async def main(args):
    if args.mentions and not args.cases:
        raise ValueError('Group mentions require existing case-topic routing (--cases).')
    if args.reports and not args.cases:
        raise ValueError('Final reports require the configured case-topic routing (--cases).')
    target = group_target(args.home, args.chat_id)
    from dotenv import dotenv_values
    from tools.send_message_senders import _telegram_bot
    secrets = dotenv_values(Path(args.home) / '.env')
    token = secrets.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('포식이 연결 설정에 Telegram 봇 토큰이 필요합니다.')
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with _telegram_bot(token) as bot:
            chat = await bot.get_chat(target)
            if chat.type not in ('group', 'supergroup') or (args.group and chat.title != args.group):
                raise ValueError('지정한 그룹 ID와 이름이 일치하지 않습니다.')
            if args.cases:
                identity = await bot.get_me()
                member = await bot.get_chat_member(chat.id, identity.id)
                if not chat.is_forum or not getattr(member, 'can_manage_topics', False):
                    raise ValueError('그룹 토픽과 봇의 토픽 관리 권한을 켜주세요.')
                router = TopicRouter(args.home, args.intake, args.state, chat.id, args.session, args.legacy_state)
                from telegram_actions import ChatActions
                actions = ChatActions(bot, chat.id, lambda: set())
                mentions = None
                if args.mentions:
                    from telegram_mentions import Mentions
                    webhook = await bot.get_webhook_info()
                    if webhook.url:
                        raise ValueError('Mention polling requires the existing bot webhook to be removed explicitly.')
                    mentions = Mentions(args.home, args.intake, Path(args.home).parent / 'telegram-mentions.json',
                        chat.id, identity.id, identity.username, lambda: router.state['routes'])
                async def create_topic(name):
                    topic = await bot.create_forum_topic(chat.id, name=name)
                    return topic.message_thread_id
                async def send_topic(thread_id, text):
                    result = await bot.send_message(chat_id=chat.id, message_thread_id=thread_id, text=text,
                                                    parse_mode=None, disable_web_page_preview=True,
                                                    disable_notification=False)
                    return result.message_id
                async def send_report(thread_id, name, payload, caption):
                    from telegram import InputFile
                    async with actions.upload_document(thread_id):
                        result = await bot.send_document(chat_id=chat.id, message_thread_id=thread_id,
                            document=InputFile(payload, filename=name), caption=caption,
                            parse_mode=None, disable_notification=False)
                    return result.message_id
                async def update_progress(thread_id, message_id, text):
                    if message_id:
                        from telegram.error import BadRequest
                        try:
                            await bot.edit_message_text(chat_id=chat.id, message_id=message_id, text=text,
                                                        parse_mode=None, disable_web_page_preview=True)
                        except BadRequest as exc:
                            if 'message is not modified' not in str(exc).lower():
                                raise
                        return message_id
                    result = await bot.send_message(chat_id=chat.id, message_thread_id=thread_id, text=text,
                        parse_mode=None, disable_web_page_preview=True, disable_notification=True)
                    return result.message_id
                print('Important case notifications enabled; finalized report delivery ' + ('enabled.' if args.reports else 'disabled.'), flush=True)
                actions.start()
                try:
                    while True:
                        if mentions:
                            try:
                                await mentions.tick(bot)
                            except Exception as exc:
                                # Inbound network failures must not silence outbound
                                # conversation/progress delivery. Never log token URLs.
                                mentions.state['poll_error_type'] = type(exc).__name__
                                mentions.save()
                            else:
                                if mentions.state.pop('poll_error_type', None):
                                    mentions.save()
                        await router.tick(create_topic, send_topic, send_document=send_report if args.reports else None)
                        if args.once:
                            return
                        await asyncio.sleep(2)
                finally:
                    await actions.close()
            mirror = Mirror(args.home, args.intake, args.state, args.session, chat.id)
            mirror.initialize()
            async def send(text):
                result = await bot.send_message(chat_id=chat.id, text=text, parse_mode=None,
                                                disable_web_page_preview=True)
                return result.message_id
            print('Important notifications enabled for the selected conversation.', flush=True)
            while True:
                await mirror.tick(send)
                if args.once:
                    return
                await asyncio.sleep(2)


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', default=str(root / 'state/hermes'))
    p.add_argument('--intake', default=str(root / 'state/intake'))
    p.add_argument('--state', default=str(root / 'state/telegram-mirror.json'))
    p.add_argument('--session', required=True)
    p.add_argument('--cases', action='store_true', help='Route existing selected case and future real cases to forum topics')
    p.add_argument('--mentions', action='store_true', help='Accept @assistant mentions in ready case topics; queue on the existing native session')
    p.add_argument('--reports', action='store_true', help='Send future explicitly finalized HTML/Word reports to the configured case topics')
    p.add_argument('--legacy-state', default=str(root / 'state/telegram-mirror.json'))
    p.add_argument('--chat-id', help='Group ID or public @username/URL; omitted uses saved connection settings')
    p.add_argument('--group', default='', help='Optional expected group title')
    p.add_argument('--once', action='store_true')
    try:
        asyncio.run(main(p.parse_args()))
    except Exception as exc:
        # Network exception URLs can contain the bot token; never log their body.
        print('Telegram mirror stopped:', type(exc).__name__, flush=True)
        raise SystemExit(1)
