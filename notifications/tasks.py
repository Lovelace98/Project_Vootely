from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery import shared_task

from .adapters import NotificationDeliveryError
from .models import Notification
from .services import notifications_ready_for_retry, queue_event_reminders, send_notification_now, queue_voter_turnout_reminders


@shared_task(name='notifications.send_notification')
def send_notification(notification_id):
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        if notification.status == Notification.Status.SENT:
            return notification.pk
        if notification.status == Notification.Status.PROCESSING:
            return notification.pk

        notification.status = Notification.Status.PROCESSING
        notification.last_attempt_at = timezone.now()
        notification.attempt_count += 1
        notification.failure_reason = ''
        notification.save(
            update_fields=['status', 'last_attempt_at', 'attempt_count', 'failure_reason']
        )

    try:
        result = send_notification_now(notification)
        if result.deliveries <= 0:
            raise RuntimeError('Notification backend reported zero deliveries.')
    except Exception as exc:
        update_kwargs = {
            'status': Notification.Status.FAILED,
            'failure_reason': str(exc),
        }
        if isinstance(exc, NotificationDeliveryError):
            update_kwargs.update(
                provider=exc.provider,
                provider_status=exc.provider_status,
                provider_payload=exc.provider_payload,
                provider_error_code=exc.provider_error_code,
            )
        Notification.objects.filter(pk=notification.pk).update(**update_kwargs)
        return notification.pk

    Notification.objects.filter(pk=notification.pk).update(
        status=Notification.Status.SENT,
        sent_at=timezone.now(),
        provider=result.provider,
        provider_status=result.provider_status,
        provider_payload=result.provider_payload,
        provider_error_code=result.provider_error_code,
        provider_message_id=result.provider_message_id,
    )
    return notification.pk


@shared_task(name='notifications.retry_failed_notifications')
def retry_failed_notifications():
    retried = []
    for notification in notifications_ready_for_retry():
        notification.status = Notification.Status.QUEUED
        notification.save(update_fields=['status'])
        send_notification.delay(notification.pk)
        retried.append(notification.pk)
    return retried


@shared_task(name='notifications.scan_event_reminders')
def scan_event_reminders():
    return queue_event_reminders()


@shared_task(name='notifications.scan_voter_turnout_reminders')
def scan_voter_turnout_reminders():
    return queue_voter_turnout_reminders()
