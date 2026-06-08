from django.core.cache import cache


def request_identifier(request, *, scope='ip'):
    if scope == 'user' and getattr(request, 'user', None) and request.user.is_authenticated:
        return f'user:{request.user.pk}'
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def is_rate_limited(request, key_prefix, limit, window_seconds, *, scope='ip'):
    identifier = request_identifier(request, scope=scope)
    cache_key = f'{key_prefix}:{identifier}'
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.add(cache_key, 1, timeout=window_seconds)
        return False
    if count == 1:
        cache.expire(cache_key, window_seconds)
    return count > limit
