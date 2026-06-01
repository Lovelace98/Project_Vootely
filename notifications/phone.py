import re

from django.core.exceptions import ValidationError


def normalize_phone_number(value, *, strict=False):
    raw_value = (value or '').strip()
    if not raw_value:
        return ''

    cleaned = re.sub(r'[^\d+]', '', raw_value)
    if cleaned.startswith('00'):
        cleaned = f'+{cleaned[2:]}'

    if cleaned.startswith('+'):
        digits = cleaned[1:]
        if digits.isdigit() and 8 <= len(digits) <= 15:
            return f'+{digits}'
    elif cleaned.isdigit():
        if len(cleaned) == 10 and cleaned.startswith('0'):
            return f'+233{cleaned[1:]}'
        if len(cleaned) == 9:
            return f'+233{cleaned}'
        if 8 <= len(cleaned) <= 15:
            return f'+{cleaned}'

    if strict:
        raise ValidationError('Enter a valid phone number, for example +233241234567.')
    return ''


def format_hubtel_phone_number(value):
    normalized = normalize_phone_number(value)
    if not normalized:
        return ''
    return normalized.lstrip('+')
