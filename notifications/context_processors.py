from .models import InAppNotification

def unread_notifications(request):
    if request.user.is_authenticated:
        count = InAppNotification.objects.filter(user=request.user, is_read=False).count()
        recent = InAppNotification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:3]
        return {
            'unread_notifications_count': count,
            'recent_notifications': list(recent)
        }
    return {
        'unread_notifications_count': 0,
        'recent_notifications': []
    }
