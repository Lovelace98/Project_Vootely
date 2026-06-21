from django.conf import settings
from django.db import OperationalError


def canonical_url(request):
    base = getattr(settings, 'PUBLIC_APP_URL', '') or ''
    base = base.strip().rstrip('/')
    if not base:
        return {
            'canonical_url': request.build_absolute_uri(),
        }
    return {
        'canonical_url': f"{base}{request.path}",
    }

def support_contact(request):
    return {
        'support_email': settings.SUPPORT_EMAIL,
        'support_phone': settings.SUPPORT_PHONE,
        'support_name': getattr(settings, 'SUPPORT_NAME', 'Vootely'),
    }

def dashboard_greeting(request):
    from django.utils import timezone
    try:
        if not request.user.is_authenticated:
            return {}
    except OperationalError:
        return {}
    now = timezone.localtime(timezone.now())
    hour = now.hour
    if hour < 12:
        greeting = 'Good morning'
    elif hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'
    return {
        'dashboard_greeting': greeting,
    }
