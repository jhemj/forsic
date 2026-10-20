"""Case-bound references to exact retained versions; no lookup registry or latest selection."""
import base64
import hashlib
import hmac
import json

from .model_pages import RetainedSourceError, failed_source


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _decode(value):
    raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    if _encode(raw) != value:
        raise ValueError('Noncanonical handle encoding')
    return raw


def _checksum(case, payload):
    # This is an integrity/binding check, not an authorization credential. The
    # ledger remains the authority for whether the exact id/version exists.
    binding = json.dumps([str(case.db.resolve()), case.config['case_id']],
                         ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(b'forsic-source-handle-v1\0' + binding + b'\0' + payload).digest()[:16]


class SourceHandleError(RetainedSourceError):
    def __init__(self, args, reason):
        self.details = {'error_code': 'source_handle_rejected',
                        'failed_source': failed_source(args, args.get('source_id')),
                        'next_call': {'action': 'state'},
                        'next_step': 'Reselect the retained source from this case or its successful result and copy the returned source_handle/read_call. No version was inferred or replaced.'}
        super().__init__('Source selection rejected: ' + reason + '. Reselect the retained source; no source page was returned.')


def source_handle(case, source):
    """Mint a deterministic selection for this ledger and complete result hash."""
    payload = json.dumps([source['id'], source['version']], ensure_ascii=False,
                         separators=(',', ':')).encode()
    return 'sh1.' + _encode(payload) + '.' + _encode(_checksum(case, payload))


def _unpack(handle):
    if not isinstance(handle, str) or len(handle) > 2048:
        raise ValueError('Invalid source handle')
    prefix, payload64, checksum64 = handle.split('.')
    if prefix != 'sh1':
        raise ValueError('Unsupported source handle')
    payload, checksum = _decode(payload64), _decode(checksum64)
    if len(checksum) != 16:
        raise ValueError('Invalid source checksum')
    selected = json.loads(payload)
    if (not isinstance(selected, list) or len(selected) != 2
            or any(not isinstance(value, str) or not value.strip() for value in selected)):
        raise ValueError('Invalid source selection')
    return {'id': selected[0], 'version': selected[1]}, payload, checksum


def handle_reference(handle):
    """Inspect encoded identity for presentation only; does not verify case binding."""
    try:
        return _unpack(handle)[0]
    except (ValueError, TypeError, UnicodeError):
        return None


def resolve_source_handle(case, args):
    """Expand a handle, rejecting any explicit conflicting id/version."""
    if 'source_handle' not in args:
        return args
    try:
        selected, payload, checksum = _unpack(args['source_handle'])
        if not hmac.compare_digest(checksum, _checksum(case, payload)):
            raise ValueError('Handle is damaged or belongs to another case ledger')
    except (ValueError, TypeError, UnicodeError):
        raise SourceHandleError(args, 'source_handle is invalid, damaged, or belongs to another case ledger') from None
    source_id, source_version = selected['id'], selected['version']
    if any(key in args and args[key] != value
           for key, value in (('source_id', source_id), ('source_version', source_version))):
        raise SourceHandleError(args, 'explicit source_id/source_version conflicts with source_handle')
    return {**args, 'source_id': source_id, 'source_version': source_version}
