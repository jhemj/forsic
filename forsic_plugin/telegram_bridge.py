"""Case-scoped admission into Hermes' existing durable, idle-boundary mailbox."""
import hashlib
import json
from pathlib import Path

from .investigations import Investigations


def is_telegram_input(home, text):
    """Match admitted text, not a spoofable prefix, for origin and mirror routing."""
    path = Path(home).parent / 'telegram-mentions.json'
    if not path.exists():
        return False
    digest = hashlib.sha256(str(text).encode()).hexdigest()
    state = json.loads(path.read_text())
    return any(row.get('text_sha256') == digest for row in state.get('requests', {}).values())


def admit(home, intake, request):
    """No session creation, model call, interruption or terminal/slash dispatch."""
    from tools.bot_live_delivery import (
        deliver_to_live_owner, find_canonical_live_owner, read_delivery_result, rebind_queued_delivery,
        request_live_owner, claim_requested_owner)
    item = Investigations(home, intake).get(case_id=request['case_id'])
    if not item or item['session_id'] != request['session_id'] or item['archived']:
        return {'status': 'binding_changed'}
    previous = read_delivery_result(home, request['delivery_id'])
    if previous is not None:
        if previous.get('message') != request['text'] or previous.get('author') != request.get('author'):
            raise ValueError('delivery payload mismatch')
        if previous['status'] == 'queued':
            owner = find_canonical_live_owner(home, request['session_id'])
            if owner is not None:
                previous = rebind_queued_delivery(home, request['delivery_id'], owner)
        return {'status': previous['status'], 'delivery_id': previous['delivery_id']}
    owner = find_canonical_live_owner(home, request['session_id'])
    if owner is None:
        request_live_owner(home, request['session_id'])
        return {'status': 'waiting_session'}
    claim_requested_owner(home, owner['session_id'], lambda: None)
    # Compaction may already have rotated the native writer but not yet loaded
    # the plugin hook that inherits its case binding. Only that lineage is used.
    row = deliver_to_live_owner(home, owner, request['text'],
        delivery_id=request['delivery_id'], author=request['author'])
    return {'status': row['status'], 'delivery_id': row['delivery_id']}
