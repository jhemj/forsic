"""Value-free tool argument observations; never change a request or repair input."""
import hashlib
import json


_FIELDS = ("path", "file_path", "action", "mission_id", "mission_version",
           "tool_name", "tool_arguments", "member_index")


def _json_measure(value):
    """Streaming metadata only; never retain a second serialized request body."""
    digest = hashlib.sha256()
    size = 0
    for part in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True).iterencode(value):
        raw = part.encode('utf-8')
        size += len(raw)
        digest.update(raw)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def argument_shape(raw):
    """Parse only a complete JSON value. Do not expose values or unknown field names."""
    if not isinstance(raw, str):
        return {"json_state": "not_string"}
    result = {"bytes": len(raw.encode("utf-8", errors="replace"))}
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return dict(result, json_state="invalid")
    if not isinstance(value, dict):
        return dict(result, json_state="not_object")
    def state(key):
        if key not in value:
            return "absent"
        item = value[key]
        if item is None:
            return "null"
        if isinstance(item, str):
            return "string" if item else "empty_string"
        return {bool: "boolean", int: "integer", float: "number",
                dict: "object", list: "array"}.get(type(item), "other")
    return dict(result, json_state="object", fields={key: state(key) for key in _FIELDS})


def request_boundary(api_kwargs):
    """Observe the pre-SDK body with extra_body precedence, not an HTTP wire capture."""
    body = dict(api_kwargs or {})
    extra = body.pop("extra_body", None)
    if isinstance(extra, dict):
        body.update(extra)
    tools = body.get("tools")
    messages = body.get("messages")
    summary = {"stage": "pre_sdk_merged_body", "http_wire_observed": False,
               "tool_count": len(tools) if isinstance(tools, list) else None,
               "message_count": len(messages) if isinstance(messages, list) else None}
    if isinstance(tools, list):
        measured = _json_measure(tools)
        summary["ordered_tools_sha256"] = measured['sha256']
        summary['tools_json'] = measured
    if isinstance(messages, list):
        summary['messages_json'] = _json_measure(messages)
        summary['message_json_by_role'] = {}
        for message in messages:
            role = message.get('role') if isinstance(message, dict) else None
            if role not in ('system', 'developer', 'user', 'assistant', 'tool'):
                role = 'other'
            group = summary['message_json_by_role'].setdefault(role, {'count': 0, 'bytes': 0})
            group['count'] += 1
            group['bytes'] += _json_measure(message)['bytes']
        summary['measurement_basis'] = 'canonical_json_utf8_not_tokens_or_http_wire; role_bytes_exclude_array_separators'
    return summary


def response_boundary(tool_calls):
    """No names, IDs, paths or content; SDK-parsed calls are not raw server generation."""
    calls = []
    for call in tool_calls[:32]:
        fn = getattr(call, "function", None)
        row = {"parsed": argument_shape(getattr(fn, "arguments", None)),
               "args_repaired": getattr(fn, "args_repaired", False) is True}
        before = getattr(fn, "_arguments_before_repair_shape", None)
        if isinstance(before, dict):
            row["accumulated_before_repair"] = before
        calls.append(row)
    return {"stage": "sdk_parsed_tool_calls", "provider_raw_observed": False,
            "calls": calls, "omitted_calls": max(0, len(tool_calls) - len(calls))}
