"""Optional observations over returned listing names; no filesystem access or verdicts.

Attach the result before retaining a listing, so each field_pointer addresses
the unchanged name in that retained result. An enclosing evidence receipt is
the source when source_ref is not yet available. Re-reading does not create a
new observation identity for the same origin/name. IDs are deduplication keys,
not source versions or proof of authenticity.
"""
import hashlib
import json
import unicodedata


BIDI_CONTROLS = frozenset('\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')
HIDDEN_CONTROLS = frozenset('\u00ad\u200b\u2060\ufeff')


MAX_OUTPUT_BYTES = 65536
MAX_LOCATOR_CHARACTERS = 256


def _text_digest(value):
    digest = hashlib.sha256()
    for offset in range(0, len(value), 1024):
        digest.update(value[offset:offset + 1024].encode('utf-8', errors='surrogatepass'))
    return digest.hexdigest()


def _identity_digest(locator):
    # Hash original identity fields incrementally; display omission never merges
    # distinct origins and no unbounded JSON copy of a parser pathname is made.
    digest = hashlib.sha256()
    for key, value in sorted(locator.items()):
        digest.update(key.encode('ascii') + b'\x00')
        if isinstance(value, str):
            digest.update(b'str:' + str(len(value)).encode('ascii') + b':')
            digest.update(_text_digest(value).encode('ascii'))
        else:
            digest.update(b'int:' + str(value).encode('ascii'))
        digest.update(b'\x00')
    return digest.hexdigest()


def _bounded_value(value, pointer=None):
    if not isinstance(value, str) or len(value) <= MAX_LOCATOR_CHARACTERS:
        return value
    result = {'omitted': True, 'reason': 'locator_character_limit',
              'characters': len(value), 'sha256': _text_digest(value),
              'digest_encoding': 'utf-8-surrogatepass'}
    if pointer is not None:
        result['field_pointer'] = pointer
    return result


def observe_listing(result, *, source_ref=None, max_entries=100, max_characters=32768):
    """Inspect at most 100 returned entries and 32768 name codepoints.

    Names exceeding the remaining character budget are left uninspected, not
    truncated or normalized. Normal RTL letters, accents, ZWJ and ZWNJ do not
    trigger observations. Added JSON is bounded to 64 KiB (ASCII-escaped default
    JSON encoding); omitted observations and locator strings remain explicit.
    Directional controls may be legitimate; they are
    reported without maliciousness, confidence or extension-spoofing verdicts.
    """
    if type(max_entries) is not int or not 0 <= max_entries <= 100:
        raise ValueError('max_entries must be between 0 and 100')
    if type(max_characters) is not int or not 0 <= max_characters <= 32768:
        raise ValueError('max_characters must be between 0 and 32768')
    if source_ref is not None and (not isinstance(source_ref, dict) or
            any(type(source_ref[key]) not in (str, int) for key in ('id', 'version') if key in source_ref)):
        raise ValueError('source_ref id/version must be scalar strings or integers')
    field = next((key for key in ('files', 'entries', 'members') if isinstance(result.get(key), list)), None)
    rows = result[field] if field else []
    observations = []
    seen = set()
    inspected = characters = invalid = budget_skipped = output_skipped = 0
    output_used = 0
    # Caller-owned original values are hashed before bounding the derived view.
    # No pathname interpretation or I/O.
    origin = {key: result[key] for key in ('path', 'file_path', 'volume_offset', 'archive_path')
              if type(result.get(key)) in (str, int)}
    if 'path' in origin:
        origin['listing_path'] = origin.pop('path')
        if 'file_path' in origin or 'volume_offset' in origin:
            origin['image_path'] = origin['listing_path']
    for index, row in enumerate(rows[:max_entries if max_characters else 0]):
        key = next((key for key in ('path', 'name') if isinstance(row, dict) and isinstance(row.get(key), str)), None)
        if key is None:
            invalid += 1
            continue
        name = row[key]
        if len(name) > max_characters - characters:
            budget_skipped += 1
            continue
        characters += len(name)
        inspected += 1
        controls = []
        count = 0
        for position, character in enumerate(name):
            if character in BIDI_CONTROLS or character in HIDDEN_CONTROLS or ord(character) < 32 or ord(character) == 127:
                count += 1
                if len(controls) < 64:
                    controls.append({'index': position, 'codepoint': f'U+{ord(character):04X}',
                                     'name': unicodedata.name(character, 'CONTROL'),
                                     'kind': 'directional_control' if character in BIDI_CONTROLS else 'hidden_control'})
        if not count:
            continue
        locator = {**origin, **{key: row[key] for key in
                   ('path', 'name', 'inode', 'member_index', 'header_offset', 'data_offset')
                   if type(row.get(key)) in (str, int)}}
        observation_id = 'filename-' + _identity_digest(locator)[:32]
        pointers = {key: '/' + ('path' if key in ('listing_path', 'image_path') else key) for key in origin}
        pointers.update({key: f'/{field}/{index}/{key}' for key in locator if key not in origin or key in row})
        locator = {key: _bounded_value(value, pointers[key]) for key, value in locator.items()}
        if observation_id in seen:
            continue
        seen.add(observation_id)
        observation = {'id': observation_id, 'kind': 'filename_display_control',
                       'raw_name': name, 'escaped_display': json.dumps(name, ensure_ascii=True),
                       'field_pointer': f'/{field}/{index}/{key}', 'locator': locator,
                       'controls': controls, 'control_count': count,
                       'omitted_control_positions': count - len(controls),
                       'meaning': 'Display controls observed; intent, rendered appearance and maliciousness are not established.'}
        if source_ref is not None:
            observation['source_ref'] = {key: _bounded_value(source_ref[key])
                                         for key in ('id', 'version') if key in source_ref}
        # Reserve enough room for the fixed scope envelope and JSON separators.
        # The name itself was already bounded by max_characters; this temporary
        # encoding cannot grow with an unbounded locator string.
        size = len(json.dumps(observation, ensure_ascii=True).encode('ascii'))
        if size + output_used > MAX_OUTPUT_BYTES - 4096:
            output_skipped += 1
            continue
        output_used += size + 2
        observations.append(observation)
    uninspected = len(rows) - inspected
    return {'status': 'not_run' if field is None or max_entries == 0 or max_characters == 0 else
            'partial' if uninspected or output_skipped else 'inspected_returned_names',
            'observations': observations,
            'scope': {'listing_field': field, 'returned_entries': len(rows),
                      'inspected_entries': inspected, 'inspected_characters': characters,
                      'uninspected_entries': uninspected, 'invalid_name_entries': invalid,
                      'character_budget_skipped_entries': budget_skipped,
                      'entry_budget_skipped_entries': max(0, len(rows) - max_entries),
                      'omitted_observations': output_skipped,
                      'observation_omission_reasons': {'output_byte_budget': output_skipped},
                      'max_added_json_bytes': MAX_OUTPUT_BYTES,
                      'max_locator_characters': MAX_LOCATOR_CHARACTERS,
                      'unreturned_entries': 'unknown', 'whole_image_inspected': False,
                      'meaning': 'Only names already returned in this listing; zero observations is not a clean-image verdict.'}}
