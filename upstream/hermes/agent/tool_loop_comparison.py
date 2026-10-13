"""Pure, profile-scoped tool comparison adapters; failures retain native behavior."""
import logging
from copy import deepcopy


def comparison(tool_name, args, result=None):
    from tools.registry import registry
    entry = registry.get_entry(tool_name)
    callback = getattr(entry, 'loop_comparison', None)
    if callback is None:
        return {}
    try:
        value = callback(deepcopy(dict(args or {})), result)
        if not isinstance(value, dict):
            raise TypeError('Comparison must be a dict')
        return value
    except Exception:
        logging.getLogger(__name__).warning('Tool comparison failed for %s; using native identity', tool_name)
        return {}
