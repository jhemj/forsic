"""Whole finite numbers in retained JSON; never original evidence text bytes."""
import json
import math

NUMBER_COORDINATES = 'UTF-8 bytes of the whole canonical JSON number in the retained tool result; not original evidence bytes or image offsets'


def number_literal(value):
    if type(value) not in (int, float) or type(value) is float and not math.isfinite(value):
        raise ValueError('Select a finite retained int/float; bool, null and composite values are not numeric citations')
    return json.dumps(value, allow_nan=False, separators=(',', ':'))


def number_identity(value):
    number_literal(value)
    return {'value': value, 'value_type': type(value).__name__, 'representation': 'canonical-json-number-v1'}


def citation_context(data, pointer):
    from .measurement import acquisition_context
    from .model_pages import select
    context = acquisition_context(data, pointer)
    try:
        value = select(data, pointer)
    except ValueError:
        # Source navigation validates absent pointers separately and returns its
        # canonical recovery calls; context decoration must not replace them.
        return context
    if type(value) in (int, float):
        context.update(field_role='metadata', retained_scalar=number_identity(value),
                       coordinate_basis=NUMBER_COORDINATES)
    return context
