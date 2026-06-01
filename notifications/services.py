from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .adapters import get_notification_adapter
from .models import Notification
from .phone import normalize_phone_number


def absolute_url(path):
    base = getattr(settings, 'PUBLIC_APP_URL', '').rstrip('/')
    if not path:
        return base or '/'
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if base:
        return f'{base}{path}'
    return path


def build_dedupe_key(channel, event_type, *parts):
    normalized = [str(part) for part in parts if part not in (None, '')]
    return ':'.join([channel, event_type, *normalized])


def render_notification_content(channel, event_type, context):
    if channel == Notification.Channel.SMS:
        body_text = render_to_string(
            f'notifications/sms/{event_type}.txt',
            context,
        )
        return Notification.EventType(event_type).label, body_text.strip(), ''

    subject = render_to_string(
        f'notifications/email/{event_type}_subject.txt',
        context,
    )
    body_text = render_to_string(
        f'notifications/email/{event_type}.txt',
        context,
    )
    try:
        body_html = render_to_string(
            f'notifications/email/{event_type}.html',
            context,
        )
    except TemplateDoesNotExist:
        body_html = ''
    return ' '.join(subject.splitlines()).strip(), body_text.strip(), body_html.strip()


def get_staff_notification_recipients():
    configured = [
        email.strip()
        for email in getattr(settings, 'NOTIFICATION_ADMIN_EMAILS', [])
        if email.strip()
    ]
    if configured:
        return [{'email': email, 'name': 'VoteCentral Staff'} for email in configured]

    user_model = get_user_model()
    return [
        {'email': email, 'name': 'VoteCentral Staff'}
        for email in user_model.objects.filter(is_staff=True)
        .exclude(email='')
        .values_list('email', flat=True)
    ]


def get_notification_context(event_type, *, event=None, payment_attempt=None, withdrawal_request=None):
    return {
        'notification_event_type': event_type,
        'public_app_url': getattr(settings, 'PUBLIC_APP_URL', '').rstrip('/'),
        'support_email': getattr(settings, 'SERVER_EMAIL', ''),
        'event': event,
        'payment_attempt': payment_attempt,
        'withdrawal_request': withdrawal_request,
        'event_title': event.title if event else '',
        'event_start_at': event.start_at if event else None,
        'event_end_at': event.end_at if event else None,
        'event_public_url': absolute_url(event.get_absolute_url()) if event else absolute_url(reverse('events:home')),
        'event_dashboard_url': absolute_url(event.get_dashboard_url()) if event else '',
        'payment_reference': payment_attempt.gateway_reference if payment_attempt else '',
        'payment_status_url': absolute_url(
            reverse('payments:status_detail', args=[payment_attempt.gateway_reference])
        )
        if payment_attempt
        else '',
        'nominee_name': payment_attempt.nominee.name if payment_attempt else '',
        'vote_quantity': payment_attempt.vote_quantity if payment_attempt else '',
        'payment_amount': payment_attempt.amount if payment_attempt else '',
        'payment_currency': payment_attempt.currency if payment_attempt else '',
        'withdrawal_amount': withdrawal_request.amount if withdrawal_request else '',
        'withdrawal_currency': withdrawal_request.currency if withdrawal_request else '',
        'withdrawal_dashboard_url': absolute_url(reverse('dashboard:withdrawals')),
        'revenue_dashboard_url': absolute_url(reverse('dashboard:revenue')),
        'review_notes': withdrawal_request.review_notes if withdrawal_request else '',
        'payout_reference': withdrawal_request.payout_reference if withdrawal_request else '',
        'withdrawal_admin_url': absolute_url(
            reverse('admin:wallets_withdrawalrequest_change', args=[withdrawal_request.pk])
        )
        if withdrawal_request
        else '',
    }


def dispatch_notification(notification_id):
    from .tasks import send_notification

    def enqueue():
        try:
            send_notification.delay(notification_id)
        except Exception as exc:
            Notification.objects.filter(pk=notification_id).update(
                status=Notification.Status.FAILED,
                failure_reason=f'Queue dispatch failed: {exc}',
                last_attempt_at=timezone.now(),
            )

    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(enqueue)
    else:
        enqueue()


def queue_notification(
    *,
    channel=Notification.Channel.EMAIL,
    event_type,
    recipient_email='',
    recipient_phone='',
    recipient_name='',
    event=None,
    payment_attempt=None,
    withdrawal_request=None,
    dedupe_parts=(),
):
    if channel == Notification.Channel.EMAIL and not recipient_email:
        return None
    if channel == Notification.Channel.SMS and not recipient_phone:
        return None

    context = get_notification_context(
        event_type,
        event=event,
        payment_attempt=payment_attempt,
        withdrawal_request=withdrawal_request,
    )
    subject, body_text, body_html = render_notification_content(channel, event_type, context)
    notification, created = Notification.objects.get_or_create(
        dedupe_key=build_dedupe_key(channel, event_type, *dedupe_parts),
        defaults={
            'channel': channel,
            'event_type': event_type,
            'recipient_email': recipient_email,
            'recipient_phone': recipient_phone,
            'recipient_name': recipient_name,
            'event': event,
            'payment_attempt': payment_attempt,
            'withdrawal_request': withdrawal_request,
            'subject': subject,
            'body_text': body_text,
            'body_html': body_html,
            'provider': 'hubtel' if channel == Notification.Channel.SMS else 'django-email',
        },
    )
    if created:
        dispatch_notification(notification.pk)
    return notification


def sms_channel_ready():
    return bool(
        getattr(settings, 'SMS_PROVIDER', '').strip().lower() == 'hubtel'
        and settings.HUBTEL_CLIENT_ID
        and settings.HUBTEL_CLIENT_SECRET
        and settings.HUBTEL_SMS_FROM
    )


def queue_sms_notification(
    *,
    event_type,
    recipient_phone,
    recipient_name='',
    event=None,
    payment_attempt=None,
    withdrawal_request=None,
    dedupe_parts=(),
):
    normalized_phone = normalize_phone_number(recipient_phone)
    if not normalized_phone or not sms_channel_ready():
        return None
    return queue_notification(
        channel=Notification.Channel.SMS,
        event_type=event_type,
        recipient_phone=normalized_phone,
        recipient_name=recipient_name,
        event=event,
        payment_attempt=payment_attempt,
        withdrawal_request=withdrawal_request,
        dedupe_parts=dedupe_parts,
    )


def send_notification_now(notification):
    adapter = get_notification_adapter(notification)
    return adapter.send(notification)


def queue_organizer_notifications(
    *,
    event_type,
    organizer,
    recipient_name='',
    event=None,
    withdrawal_request=None,
    dedupe_parts=(),
):
    notifications = []
    email_notification = queue_notification(
        event_type=event_type,
        recipient_email=organizer.email,
        recipient_name=recipient_name or organizer.email,
        event=event,
        withdrawal_request=withdrawal_request,
        dedupe_parts=dedupe_parts,
    )
    if email_notification is not None:
        notifications.append(email_notification)

    if organizer.sms_opt_in:
        sms_notification = queue_sms_notification(
            event_type=event_type,
            recipient_phone=organizer.phone_number,
            recipient_name=recipient_name or organizer.email,
            event=event,
            withdrawal_request=withdrawal_request,
            dedupe_parts=dedupe_parts,
        )
        if sms_notification is not None:
            notifications.append(sms_notification)
    return notifications


def queue_payment_confirmed(payment_attempt):
    notifications = []
    
    # Create in-app audit log / notification for organizer
    create_in_app_notification(
        user=payment_attempt.event.owner,
        title="New Vote Purchase Confirmed!",
        message=f"Received a purchase of {payment_attempt.vote_quantity} votes for nominee '{payment_attempt.nominee.name}' in event '{payment_attempt.event.title}'. Amount: {payment_attempt.currency} {payment_attempt.amount}.",
        link=payment_attempt.event.get_dashboard_url(),
        level='success',
        event=payment_attempt.event,
        nominee=payment_attempt.nominee,
        payment_attempt=payment_attempt,
    )

    email_notification = queue_notification(
        event_type=Notification.EventType.PAYMENT_CONFIRMED,
        recipient_email=payment_attempt.voter_email,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.PAYMENT_CONFIRMED,
        recipient_phone=payment_attempt.voter_phone,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)
    return notifications


def queue_payment_failed(payment_attempt):
    notifications = []

    # Create in-app audit log for organizer
    create_in_app_notification(
        user=payment_attempt.event.owner,
        title="Payment Failed",
        message=f"A payment attempt for {payment_attempt.vote_quantity} votes for nominee '{payment_attempt.nominee.name}' in event '{payment_attempt.event.title}' has failed. Amount: {payment_attempt.currency} {payment_attempt.amount}.",
        link=payment_attempt.event.get_dashboard_url(),
        level='danger',
        event=payment_attempt.event,
        nominee=payment_attempt.nominee,
        payment_attempt=payment_attempt,
    )

    email_notification = queue_notification(
        event_type=Notification.EventType.PAYMENT_FAILED,
        recipient_email=payment_attempt.voter_email,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.PAYMENT_FAILED,
        recipient_phone=payment_attempt.voter_phone,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)
    return notifications


def queue_payment_cancelled(payment_attempt):
    notifications = []

    # Create in-app audit log for organizer
    create_in_app_notification(
        user=payment_attempt.event.owner,
        title="Payment Cancelled",
        message=f"A payment of {payment_attempt.currency} {payment_attempt.amount} for nominee '{payment_attempt.nominee.name}' in event '{payment_attempt.event.title}' was cancelled by the voter.",
        link=payment_attempt.event.get_dashboard_url(),
        level='warning',
        event=payment_attempt.event,
        nominee=payment_attempt.nominee,
        payment_attempt=payment_attempt,
    )

    email_notification = queue_notification(
        event_type=Notification.EventType.PAYMENT_CANCELLED,
        recipient_email=payment_attempt.voter_email,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.PAYMENT_CANCELLED,
        recipient_phone=payment_attempt.voter_phone,
        recipient_name=payment_attempt.voter_name,
        event=payment_attempt.event,
        payment_attempt=payment_attempt,
        dedupe_parts=(payment_attempt.pk,),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)
    return notifications


def queue_withdrawal_requested_notifications(withdrawal_request):
    # Create in-app audit log / notification for organizer
    create_in_app_notification(
        user=withdrawal_request.organizer,
        title="Withdrawal Requested",
        message=f"A payout request of {withdrawal_request.currency} {withdrawal_request.amount} has been initiated and is currently pending review.",
        link="/dashboard/withdrawals/",
        level='info',
        withdrawal_request=withdrawal_request,
    )
    
    notifications = queue_organizer_notifications(
        event_type=Notification.EventType.WITHDRAWAL_REQUESTED,
        organizer=withdrawal_request.organizer,
        recipient_name=withdrawal_request.organizer.email,
        withdrawal_request=withdrawal_request,
        dedupe_parts=(withdrawal_request.pk,),
    )
    for recipient in get_staff_notification_recipients():
        notification = queue_notification(
            event_type=Notification.EventType.WITHDRAWAL_REVIEW_REQUIRED,
            recipient_email=recipient['email'],
            recipient_name=recipient['name'],
            withdrawal_request=withdrawal_request,
            dedupe_parts=(withdrawal_request.pk, recipient['email']),
        )
        if notification is not None:
            notifications.append(notification)
    return notifications


def queue_withdrawal_status_notification(withdrawal_request, status):
    status_titles = {
        withdrawal_request.Status.APPROVED: "Withdrawal Approved",
        withdrawal_request.Status.PROCESSING: "Withdrawal Processing",
        withdrawal_request.Status.COMPLETED: "Withdrawal Completed Successfully",
        withdrawal_request.Status.REJECTED: "Withdrawal Rejected",
    }
    status_levels = {
        withdrawal_request.Status.APPROVED: "success",
        withdrawal_request.Status.PROCESSING: "info",
        withdrawal_request.Status.COMPLETED: "success",
        withdrawal_request.Status.REJECTED: "danger",
    }
    title = status_titles.get(status, f"Withdrawal Status: {status.title()}")
    level = status_levels.get(status, "info")
    
    # Create in-app audit log / notification for organizer
    create_in_app_notification(
        user=withdrawal_request.organizer,
        title=title,
        message=f"Your withdrawal request of {withdrawal_request.currency} {withdrawal_request.amount} has been {status.lower()}.",
        link="/dashboard/withdrawals/",
        level=level,
        withdrawal_request=withdrawal_request,
    )

    status_map = {
        withdrawal_request.Status.APPROVED: Notification.EventType.WITHDRAWAL_APPROVED,
        withdrawal_request.Status.PROCESSING: Notification.EventType.WITHDRAWAL_PROCESSING,
        withdrawal_request.Status.COMPLETED: Notification.EventType.WITHDRAWAL_COMPLETED,
        withdrawal_request.Status.REJECTED: Notification.EventType.WITHDRAWAL_REJECTED,
    }
    event_type = status_map.get(status)
    if not event_type:
        return []
    return queue_organizer_notifications(
        event_type=event_type,
        organizer=withdrawal_request.organizer,
        recipient_name=withdrawal_request.organizer.email,
        withdrawal_request=withdrawal_request,
        dedupe_parts=(withdrawal_request.pk, status),
    )


def queue_event_notification(event, event_type):
    if event_type == Notification.EventType.EVENT_PUBLISHED and event.published_at:
        marker = event.published_at.isoformat()
        create_in_app_notification(
            user=event.owner,
            title="Event Published Successfully",
            message=f"Your event '{event.title}' is now public and accepting votes!",
            link=event.get_dashboard_url(),
            level='success',
            event=event,
        )
    elif event_type == Notification.EventType.EVENT_CLOSED:
        marker = 'closed'
        create_in_app_notification(
            user=event.owner,
            title="Event Closed",
            message=f"Your event '{event.title}' has been closed. Voting is now concluded.",
            link=event.get_dashboard_url(),
            level='warning',
            event=event,
        )
    else:
        marker = event.updated_at.isoformat()

    return queue_organizer_notifications(
        event_type=event_type,
        organizer=event.owner,
        recipient_name=event.owner.email,
        event=event,
        dedupe_parts=(event.pk, marker),
    )


def queue_event_reminders(reference_time=None):
    from events.models import Event

    reference_time = reference_time or timezone.now()
    lead = timedelta(hours=settings.NOTIFICATION_REMINDER_LEAD_HOURS)
    horizon = reference_time + lead
    starting_events = Event.objects.filter(
        status=Event.Status.PUBLISHED,
        is_public=True,
        start_at__gt=reference_time,
        start_at__lte=horizon,
    ).select_related('owner')
    ending_events = Event.objects.filter(
        status=Event.Status.PUBLISHED,
        is_public=True,
        end_at__gt=reference_time,
        end_at__lte=horizon,
    ).select_related('owner')

    created = []
    for event in starting_events:
        notifications = queue_organizer_notifications(
            event_type=Notification.EventType.EVENT_STARTING_SOON,
            organizer=event.owner,
            recipient_name=event.owner.email,
            event=event,
            dedupe_parts=(event.pk, event.start_at.isoformat(), settings.NOTIFICATION_REMINDER_LEAD_HOURS),
        )
        created.extend(notification.pk for notification in notifications)
    for event in ending_events:
        notifications = queue_organizer_notifications(
            event_type=Notification.EventType.EVENT_ENDING_SOON,
            organizer=event.owner,
            recipient_name=event.owner.email,
            event=event,
            dedupe_parts=(event.pk, event.end_at.isoformat(), settings.NOTIFICATION_REMINDER_LEAD_HOURS),
        )
        created.extend(notification.pk for notification in notifications)
    return created


def notifications_ready_for_retry():
    return Notification.objects.filter(
        status=Notification.Status.FAILED,
        attempt_count__lt=settings.NOTIFICATION_RETRY_LIMIT,
    )


def create_in_app_notification(
    user,
    title,
    message,
    link='',
    level='info',
    event=None,
    nominee=None,
    payment_attempt=None,
    withdrawal_request=None,
):
    from .models import InAppNotification
    return InAppNotification.objects.create(
        user=user,
        title=title,
        message=message,
        link=link,
        level=level,
        event=event,
        nominee=nominee,
        payment_attempt=payment_attempt,
        withdrawal_request=withdrawal_request,
    )
