"""Derive the model menu from operator preferences; never mutate recorded plans."""
from copy import deepcopy

from .capability_policy import current_policy


def schema_overrides(name, schema, policy=None):
    selected = current_policy() if policy is None else policy
    parameters = deepcopy(schema.get('parameters', {}))
    properties = parameters.get('properties', {})
    if name == 'forsic_skill':
        names = properties.get('name', {}).get('enum', [])
        enabled = [name for name in names if selected.skill_enabled(name)]
        if enabled:
            properties['name']['enum'] = enabled
        else:
            # Empty enums are rejected by some providers. The catalog-only
            # entry remains usable and returns the empty enabled list.
            properties.pop('name', None)
            properties.pop('file_path', None)
    elif name == 'forsic_mission':
        selector = properties.get('tool_name', {})
        selector['enum'] = [tool for tool in selector.get('enum', [])
                            if selected.tool_enabled(tool)]
        arguments = properties.get('tool_arguments', {})
        if 'anyOf' in arguments:
            arguments['anyOf'] = [branch for branch in arguments['anyOf']
                                 if selected.tool_enabled(branch.get('title'))]
    else:
        return {}
    return {'parameters': parameters}
