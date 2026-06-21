from django.db import OperationalError
from .models import InAppNotification
from events.performance import NOTIFICATION_CACHE_TTL, get_cache_version


def unread_notifications(request):
    try:
        if request.user.is_authenticated:
            from django.core.cache import cache

            version = get_cache_version('notifications', request.user.pk)
            cache_key = f'notifications:header:v2:user:{request.user.pk}:v{version}'
            payload = cache.get(cache_key)
            if payload is None:
                count = InAppNotification.objects.filter(user=request.user, is_read=False).count()
                recent = list(
                    InAppNotification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:3]
                )
                payload = {
                    'unread_notifications_count': count,
                    'recent_notifications': recent,
                }
                cache.set(cache_key, payload, NOTIFICATION_CACHE_TTL)
            return payload
    except OperationalError:
        pass
    return {
        'unread_notifications_count': 0,
        'recent_notifications': []
    }
