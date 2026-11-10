from django.conf import settings


def get_client_ip(request):
    """Resolve the client IP while respecting a configurable proxy depth."""
    meta = getattr(request, 'META', {}) or {}
    remote_addr = meta.get('REMOTE_ADDR', 'unknown')
    forwarded_for = (meta.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if not forwarded_for:
        return remote_addr

    try:
        trusted_proxies = int(getattr(settings, 'TRUSTED_PROXY_COUNT', 1))
    except (TypeError, ValueError):
        trusted_proxies = 1

    if trusted_proxies <= 0:
        return remote_addr

    hops = [hop.strip() for hop in forwarded_for.split(',') if hop.strip()]
    if not hops:
        return remote_addr

    index = max(0, len(hops) - trusted_proxies)
    return hops[index]
