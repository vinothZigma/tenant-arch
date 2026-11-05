"""Check submitted record data against the module's field definitions."""
import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework.exceptions import ValidationError


class TooLong(ValueError):
    pass


def clean_record_data(fields, data):
    if not isinstance(data, dict):
        raise ValidationError({'data': 'Must be an object.'})
    cleaned, errors = {}, {}
    for f in fields:
        name, ftype = f['name'], f.get('type', 'text')
        value = data.get(name)
        empty = value is None or (isinstance(value, str) and value.strip() == '')

        if ftype == 'boolean':
            cleaned[name] = bool(value)
            continue
        if empty:
            if f.get('required'):
                errors[name] = f'{f.get("label", name)} is required.'
            cleaned[name] = None
            continue
        try:
            cleaned[name] = _convert(ftype, value)
        except TooLong:
            errors[name] = f'{f.get("label", name)}: at most 255 characters.'
        except (ValueError, TypeError, DjangoValidationError):
            errors[name] = f'{f.get("label", name)}: invalid {ftype}.'
    if errors:
        raise ValidationError(errors)
    return cleaned  # unknown keys are dropped


def _convert(ftype, value):
    if ftype == 'number':
        num = float(value)
        return int(num) if num.is_integer() else num
    if ftype == 'date':
        return datetime.date.fromisoformat(str(value)).isoformat()
    value = str(value).strip()
    if ftype == 'email':
        validate_email(value)
    if ftype in ('text', 'email') and len(value) > 255:
        raise TooLong()   # the table column holds 255 characters; use a textarea field for longer text
    return value
