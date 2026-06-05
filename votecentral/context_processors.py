from django.conf import settings

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
