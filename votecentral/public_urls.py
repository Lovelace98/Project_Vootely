from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_public_base_url():
    base = (getattr(settings, 'PUBLIC_APP_URL', '') or '').strip().rstrip('/')
    if base:
        return base
    if settings.DEBUG:
        return ''
    raise ImproperlyConfigured(
        'PUBLIC_APP_URL must be set when DEBUG is False so Vootely can generate public links safely.'
    )


def build_public_url(path=''):
    if not path:
        base = get_public_base_url()
        return base or '/'
    if path.startswith('http://') or path.startswith('https://'):
        return path
    base = get_public_base_url()
    if not base:
        return path
    normalized_path = path if path.startswith('/') else f'/{path}'
    return f'{base}{normalized_path}'
