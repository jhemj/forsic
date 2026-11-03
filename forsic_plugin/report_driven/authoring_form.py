"""Named writing slots compiled into the existing versioned narrative ledger.

No inference, source selection, model calls or second report state live here.
Only unambiguous representation repairs are allowed; canonical validation still
checks content presence, reference scope and the current edit transaction.
"""
from copy import deepcopy
import re

from .template_contract import group_contract, cell_labels, row_limit, IDENTITY_VALUE_RULES


SLOTS = {
    'report_identity': ('hostname', 'representative_ip', 'acquisition_time'),
    'overall_judgment': ('conclusion',),
    'executive_context': ('question', 'answer'),
    'major_findings': ('summary', 'judgment'),
    'event_overview': ('summary',),
    'information_impact': ('finding', 'judgment'),
    'key_evidence': ('material', 'observation', 'limit'),
    'key_indicators': ('indicator', 'meaning', 'use'),
    'response_actions': ('priority', 'action', 'purpose'),
    'synthesis': ('explanation',),
    'timeline': ('time', 'event', 'interpretation'),
    'timeline_summary': ('summary',),
    'undated_events': ('summary',),
    'scope': ('scope',),
}


def _value_schema():
    return {'type': 'object', 'additionalProperties': False,
            'properties': {
                'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000, 'pattern': r'\S'},
                'detail': {'type': 'string', 'maxLength': 2000},
                'basis_keys': {'type': 'array', 'maxItems': 32, 'items': {'type': 'string'}}},
            'required': ['text', 'basis_keys']}


def input_schema(canonical):
    """Add the form at the tool boundary, retaining legacy positional callers."""
    result = deepcopy(canonical)
    entry = result['properties']['entries']['items']
    entry['properties']['values'] = {
        'type': 'object', 'additionalProperties': _value_schema(),
        'description': 'Preferred: fill the named slots in compose.form_template. Each text has its own support keys; the host supplies arrays, citations and layout.',
    }
    entry['required'] = []
    entry['oneOf'] = [{'required': ['values']}, {'required': ['cells']}]
    entry['properties']['cell_details']['minItems'] = 0
    entry['properties']['basis_keys'].pop('uniqueItems', None)
    entry['properties']['cell_basis_keys']['items'].pop('uniqueItems', None)
    entry['properties']['assessment']['type'] = ['object', 'null']
    entry['properties']['assessment']['properties']['time_range']['type'] = ['object', 'null']
    entry['properties']['event_kind']['type'] = ['string', 'null']
    entry['properties']['event_kind']['enum'].append(None)
    return result


def compact_schema(value):
    """Keep constraints in edit pages; full prose guidance is in compose."""
    if isinstance(value, dict):
        return {k: compact_schema(v) for k, v in value.items() if k != 'description'}
    return [compact_schema(v) for v in value] if isinstance(value, list) else value


def canonical_schema(group, canonical):
    result = deepcopy(canonical)
    result['properties']['entries']['maxItems'] = row_limit(group)
    entry = result['properties']['entries']['items']['properties']
    if group not in ('overall_judgment', 'major_findings'):
        entry.pop('assessment', None)
    elif group == 'overall_judgment':
        entry['assessment']['properties'].pop('time_range', None)
    if group != 'timeline':
        entry.pop('event_kind', None)
    return compact_schema(result)


def form_schema(group, canonical):
    result = input_schema(canonical)
    entry = result['properties']['entries']['items']
    for key in ('cells', 'cell_details', 'cell_basis_keys'):
        del entry['properties'][key]
    entry.pop('oneOf')
    entry['required'] = ['node_id', 'values']
    entry['properties']['values'].update(
        properties={name: _value_schema() for name in SLOTS[group]}, required=list(SLOTS[group]),
        additionalProperties=False)
    if group == 'report_identity':
        for name, pattern, limit, example in IDENTITY_VALUE_RULES:
            slot = entry['properties']['values']['properties'][name]
            slot['properties']['text'].update(pattern='^' + pattern + '$', examples=[example])
            if limit is not None:
                slot['properties']['text']['maxLength'] = limit
    if group not in ('overall_judgment', 'major_findings'):
        del entry['properties']['assessment']
    if group != 'timeline':
        del entry['properties']['event_kind']
    nodes = group_contract(group)[2]
    if nodes:
        entry['properties']['node_id'] = {'type': 'string', 'enum': list(nodes)}
    else:
        entry['properties']['node_id'] = {'type': 'string', 'pattern': '^' + group + r'\.[A-Za-z0-9_-]{1,48}$'}
    return canonical_schema(group, result)


def template(group):
    nodes = group_contract(group)[2] or (group + '.row1',)
    return {'mode': 'merge', 'complete': True, 'entries': [{'node_id': node, 'values': {
        name: {'text': '', 'detail': '', 'basis_keys': []} for name in SLOTS[group]}}
        for node in nodes]}


def slot_labels(group):
    return dict(zip(SLOTS[group], cell_labels(group)))


def to_form(group, entry):
    """Read-only projection; legacy shared support remains shared, not invented."""
    result = {k: deepcopy(v) for k, v in entry.items()
              if k in ('node_id', 'status', 'unavailable_reason', 'assessment', 'event_kind', 'basis_keys')}
    n = len(SLOTS[group])
    if len(entry.get('cells', [])) != n:
        raise ValueError('Current entry does not match the form')
    details = entry.get('cell_details', [''] * n)
    supports = entry.get('cell_basis_keys', [entry.get('basis_keys', [])] * n)
    result['values'] = {name: {'text': entry['cells'][i], 'detail': details[i],
                              'basis_keys': deepcopy(supports[i])}
                        for i, name in enumerate(SLOTS[group])}
    return result


def normalize(group, payload):
    """Return canonical input plus a receipt of lossless representation repairs."""
    result = deepcopy(payload)
    repairs = []
    if not isinstance(result, dict) or not isinstance(result.get('entries'), list):
        return result, repairs  # Canonical validation returns the typed error.
    nodes = group_contract(group)[2]
    updating = result.get('mode') in ('update', 'merge')

    def record(code, index, field):
        repairs.append({'code': code, 'entry': index, 'field': field})

    def unique(keys, index, field):
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            raise ValueError(field + ' must be a list of returned support keys')
        output = list(dict.fromkeys(keys))
        if output != keys:
            record('duplicate_support_removed', index, field)
        return output

    for index, entry in enumerate(result['entries']):
        if not isinstance(entry, dict):
            continue
        for optional in ('assessment', 'event_kind'):
            if optional in entry and entry[optional] is None:
                entry.pop(optional)
                record('null_optional_omitted', index, optional)
        assessment = entry.get('assessment')
        if isinstance(assessment, dict) and 'time_range' in assessment and assessment['time_range'] is None:
            assessment.pop('time_range')
            record('null_optional_omitted', index, 'assessment.time_range')
        node = entry.get('node_id')
        if node is None and len(nodes) == 1:
            entry['node_id'] = nodes[0]
            record('singleton_node_supplied', index, 'node_id')
        elif isinstance(node, str) and node not in nodes:
            matches = [n for n in nodes if n.rsplit('.', 1)[-1] == node]
            if len(matches) == 1:
                entry['node_id'] = matches[0]
                record('fixed_node_expanded', index, 'node_id')
        if not nodes and not updating and node is None:
            entry['node_id'] = group + '.row' + str(index + 1)
            record('row_node_supplied', index, 'node_id')
        elif not nodes and not updating and isinstance(node, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,48}', node):
            entry['node_id'] = group + '.' + node
            record('row_node_expanded', index, 'node_id')
        if 'values' in entry:
            if any(key in entry for key in ('cells', 'cell_details', 'cell_basis_keys')):
                raise ValueError('Use named values or positional cells, not both in one entry')
            values = entry.pop('values')
            if not isinstance(values, dict) or set(values) != set(SLOTS[group]):
                raise ValueError('values must contain exactly these fields: ' + ', '.join(SLOTS[group]))
            for name, value in values.items():
                if not isinstance(value, dict) or set(value) - {'text', 'detail', 'basis_keys'} or not {'text', 'basis_keys'} <= set(value):
                    raise ValueError('values.' + name + ' requires text and basis_keys, with optional detail')
            entry['cells'] = [values[name]['text'] for name in SLOTS[group]]
            entry['cell_details'] = [values[name].get('detail', '') for name in SLOTS[group]]
            entry['cell_basis_keys'] = [unique(values[name]['basis_keys'], index, 'values.' + name + '.basis_keys') for name in SLOTS[group]]
            context = unique(entry.get('basis_keys', []), index, 'basis_keys')
            entry['basis_keys'] = list(dict.fromkeys(context + [key for keys in entry['cell_basis_keys'] for key in keys]))
            if 'status' not in entry:
                entry['status'] = 'saved'
                record('saved_status_supplied', index, 'status')
            record('named_values_compiled', index, 'values')
        if entry.get('cell_details') == []:
            entry.pop('cell_details')
            record('empty_optional_details_omitted', index, 'cell_details')
        if 'basis_keys' in entry:
            entry['basis_keys'] = unique(entry['basis_keys'], index, 'basis_keys')
        if isinstance(entry.get('cell_basis_keys'), list):
            entry['cell_basis_keys'] = [unique(keys, index, 'cell_basis_keys') for keys in entry['cell_basis_keys']]
    return result, repairs
