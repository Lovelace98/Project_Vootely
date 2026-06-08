from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .adapters import get_notification_adapter, resolve_email_provider, resolve_sms_provider
from .models import Notification
from .phone import normalize_phone_number
from votecentral.public_urls import build_public_url


def absolute_url(path):
    return build_public_url(path)


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
        return [{'email': email, 'name': 'Vootely Staff'} for email in configured]

    user_model = get_user_model()
    return [
        {'email': email, 'name': 'Vootely Staff'}
        for email in user_model.objects.filter(is_staff=True)
        .exclude(email='')
        .values_list('email', flat=True)
    ]


def get_notification_context(
    event_type,
    *,
    event=None,
    payment_attempt=None,
    ticket_purchase=None,
    withdrawal_request=None,
    voter=None,
    candidate=None,
    nominee=None,
    nomination_submission=None,
    credential_token=None,
    vote_url=None,
):
    category = None
    if nomination_submission is not None:
        category = nomination_submission.category
    elif nominee is not None:
        category = nominee.category
    elif payment_attempt is not None:
        category = getattr(payment_attempt.nominee, 'category', None)
    return {
        'notification_event_type': event_type,
        'public_app_url': getattr(settings, 'PUBLIC_APP_URL', '').rstrip('/'),
        'support_email': settings.SUPPORT_EMAIL,
        'support_phone': settings.SUPPORT_PHONE,
        'support_name': getattr(settings, 'SUPPORT_NAME', 'Vootely'),
        'event': event,
        'payment_attempt': payment_attempt,
        'ticket_purchase': ticket_purchase,
        'withdrawal_request': withdrawal_request,
        'voter': voter,
        'candidate': candidate,
        'position': candidate.position if candidate else None,
        'nominee': nominee,
        'nomination_submission': nomination_submission,
        'credential_token': credential_token,
        'vote_url': vote_url,
        'event_title': event.title if event else '',
        'event_start_at': event.start_at if event else None,
        'event_end_at': event.end_at if event else None,
        'event_public_url': absolute_url(event.get_absolute_url()) if event else absolute_url(reverse('events:home')),
        'event_dashboard_url': absolute_url(event.get_dashboard_url()) if event else '',
        'event_admin_url': absolute_url(reverse('admin:events_event_change', args=[event.pk])) if event else '',
        'event_owner_email': event.owner.email if event else '',
        'platform_commission_percent': event.platform_commission_percent if event else None,
        'payment_reference': payment_attempt.gateway_reference if payment_attempt else '',
        'payment_status_url': absolute_url(
            reverse('payments:status_detail', args=[payment_attempt.gateway_reference])
        )
        if payment_attempt
        else '',
        'nominee_name': nominee.name if nominee else (payment_attempt.nominee.name if payment_attempt else ''),
        'category_name': category.name if category else '',
        'nominee_votes': getattr(nominee, 'total_votes', 0) if nominee else 0,
        'candidate_name': candidate.name if candidate else '',
        'voter_name': voter.name if voter else '',
        'vote_quantity': payment_attempt.vote_quantity if payment_attempt else '',
        'payment_amount': payment_attempt.amount if payment_attempt else '',
        'payment_currency': payment_attempt.currency if payment_attempt else '',
        'ticket_purchase_reference': ticket_purchase.gateway_reference if ticket_purchase else '',
        'ticket_purchase_url': absolute_url(ticket_purchase.get_absolute_url()) if ticket_purchase else '',
        'ticket_type_name': ticket_purchase.ticket_type.name if ticket_purchase else '',
        'ticket_quantity': ticket_purchase.quantity if ticket_purchase else '',
        'ticket_amount': ticket_purchase.amount if ticket_purchase else '',
        'ticket_currency': ticket_purchase.currency if ticket_purchase else '',
        'ticket_links': [
            absolute_url(ticket.get_absolute_url())
            for ticket in ticket_purchase.tickets.all()
        ] if ticket_purchase else [],
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
        except OSError as exc:
            Notification.objects.filter(pk=notification_id).update(
                status=Notification.Status.FAILED,
                failure_reason=f'Queue dispatch failed: {exc}',
                last_attempt_at=timezone.now(),
            )

    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(enqueue)
    else:
        enqueue()


def bulk_queue_notifications(notification_configs, event=None):
    rendered = []
    for config in notification_configs:
        context = get_notification_context(
            config['event_type'],
            event=event or config.get('event'),
            **{k: config[k] for k in ('voter', 'candidate', 'nominee', 'payment_attempt', 'ticket_purchase', 'withdrawal_request', 'nomination_submission', 'credential_token', 'vote_url') if k in config},
        )
        subject, body_text, body_html = render_notification_content(
            config['channel'], config['event_type'], context,
        )
        dedupe_key = build_dedupe_key(
            config['channel'], config['event_type'],
            *config.get('dedupe_parts', ()),
        )
        rendered.append(Notification(
            dedupe_key=dedupe_key,
            channel=config['channel'],
            event_type=config['event_type'],
            recipient_email=config.get('recipient_email', ''),
            recipient_phone=config.get('recipient_phone', ''),
            recipient_name=config.get('recipient_name', ''),
            event=event or config.get('event'),
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            provider=resolve_sms_provider() if config['channel'] == Notification.Channel.SMS else resolve_email_provider(),
        ))

    created = Notification.objects.bulk_create(rendered, ignore_conflicts=True)

    for notification in created:
        dispatch_notification(notification.pk)

    return created


def queue_notification(
    *,
    channel=Notification.Channel.EMAIL,
    event_type,
    recipient_email='',
    recipient_phone='',
    recipient_name='',
    event=None,
    payment_attempt=None,
    ticket_purchase=None,
    withdrawal_request=None,
    voter=None,
    candidate=None,
    nominee=None,
    nomination_submission=None,
    credential_token=None,
    vote_url=None,
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
        ticket_purchase=ticket_purchase,
        withdrawal_request=withdrawal_request,
        voter=voter,
        candidate=candidate,
        nominee=nominee,
        nomination_submission=nomination_submission,
        credential_token=credential_token,
        vote_url=vote_url,
    )
    subject, body_text, body_html = render_notification_content(channel, event_type, context)
    
    if channel == Notification.Channel.SMS:
        provider = resolve_sms_provider()
    else:
        provider = resolve_email_provider()

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
            'ticket_purchase': ticket_purchase,
            'withdrawal_request': withdrawal_request,
            'subject': subject,
            'body_text': body_text,
            'body_html': body_html,
            'provider': provider,
        },
    )
    if created:
        dispatch_notification(notification.pk)
    return notification


def sms_channel_ready():
    return bool(resolve_sms_provider())


def queue_sms_notification(
    *,
    event_type,
    recipient_phone,
    recipient_name='',
    event=None,
    payment_attempt=None,
    ticket_purchase=None,
    withdrawal_request=None,
    voter=None,
    candidate=None,
    nominee=None,
    nomination_submission=None,
    credential_token=None,
    vote_url=None,
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
        ticket_purchase=ticket_purchase,
        withdrawal_request=withdrawal_request,
        voter=voter,
        candidate=candidate,
        nominee=nominee,
        nomination_submission=nomination_submission,
        credential_token=credential_token,
        vote_url=vote_url,
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
    if getattr(organizer, 'email_opt_in', True):
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


def queue_ticket_purchased(ticket_purchase):
    notifications = []

    create_in_app_notification(
        user=ticket_purchase.event.owner,
        title="New Ticket Purchase Confirmed",
        message=f"Sold {ticket_purchase.quantity} {ticket_purchase.ticket_type.name} ticket(s) for '{ticket_purchase.event.title}'. Amount: {ticket_purchase.currency} {ticket_purchase.amount}.",
        link=ticket_purchase.event.get_dashboard_url(),
        level='success',
        event=ticket_purchase.event,
    )

    email_notification = queue_notification(
        event_type=Notification.EventType.TICKET_PURCHASED,
        recipient_email=ticket_purchase.buyer_email,
        recipient_name=ticket_purchase.buyer_name,
        event=ticket_purchase.event,
        ticket_purchase=ticket_purchase,
        dedupe_parts=(ticket_purchase.pk,),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.TICKET_PURCHASED,
        recipient_phone=ticket_purchase.buyer_phone,
        recipient_name=ticket_purchase.buyer_name,
        event=ticket_purchase.event,
        ticket_purchase=ticket_purchase,
        dedupe_parts=(ticket_purchase.pk,),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)

    recipient_phone = ticket_purchase.metadata.get('recipient_phone', '')
    if recipient_phone and recipient_phone != ticket_purchase.buyer_phone:
        sms_notification = queue_sms_notification(
            event_type=Notification.EventType.TICKET_PURCHASED,
            recipient_phone=recipient_phone,
            recipient_name='',
            event=ticket_purchase.event,
            ticket_purchase=ticket_purchase,
            dedupe_parts=(ticket_purchase.pk, 'recipient'),
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
        
        # Notify active nominees when competition goes live
        if event.kind == 'paid_competition':
            for nominee in event.nominees.filter(is_active=True):
                if nominee.email:
                    queue_notification(
                        channel=Notification.Channel.EMAIL,
                        event_type=Notification.EventType.NOMINEE_GOES_LIVE,
                        recipient_email=nominee.email,
                        recipient_name=nominee.name,
                        event=event,
                        nominee=nominee,
                        vote_url=absolute_url(nominee.get_absolute_url()),
                        dedupe_parts=(event.pk, nominee.pk, 'live_email'),
                    )
                if nominee.phone_number:
                    queue_sms_notification(
                        event_type=Notification.EventType.NOMINEE_GOES_LIVE,
                        recipient_phone=nominee.phone_number,
                        recipient_name=nominee.name,
                        event=event,
                        nominee=nominee,
                        vote_url=absolute_url(nominee.get_absolute_url()),
                        dedupe_parts=(event.pk, nominee.pk, 'live_sms'),
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
        
        # Notify active nominees with performance summary when competition closes
        if event.kind == 'paid_competition':
            from events.performance import build_event_leaderboard
            leaderboard = build_event_leaderboard(event)
            for nominee in leaderboard:
                if nominee.email:
                    queue_notification(
                        channel=Notification.Channel.EMAIL,
                        event_type=Notification.EventType.NOMINEE_EVENT_CLOSED,
                        recipient_email=nominee.email,
                        recipient_name=nominee.name,
                        event=event,
                        nominee=nominee,
                        dedupe_parts=(event.pk, nominee.pk, 'closed_email'),
                    )
                if nominee.phone_number:
                    queue_sms_notification(
                        event_type=Notification.EventType.NOMINEE_EVENT_CLOSED,
                        recipient_phone=nominee.phone_number,
                        recipient_name=nominee.name,
                        event=event,
                        nominee=nominee,
                        dedupe_parts=(event.pk, nominee.pk, 'closed_sms'),
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


def queue_event_commission_setup_required(event):
    notifications = []
    recipient_email = settings.SUPPORT_EMAIL
    recipient_phone = settings.SUPPORT_PHONE

    email_notification = queue_notification(
        event_type=Notification.EventType.EVENT_COMMISSION_SETUP_REQUIRED,
        recipient_email=recipient_email,
        recipient_name='Vootely Admin',
        event=event,
        dedupe_parts=(event.pk, 'commission_setup_email'),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.EVENT_COMMISSION_SETUP_REQUIRED,
        recipient_phone=recipient_phone,
        recipient_name='Vootely Admin',
        event=event,
        dedupe_parts=(event.pk, 'commission_setup_sms'),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)

    return notifications


def queue_nomination_submitted(submission):
    notifications = []

    email_notification = queue_notification(
        event_type=Notification.EventType.NOMINATION_SUBMITTED,
        recipient_email=submission.event.owner.email,
        recipient_name=submission.event.owner.email,
        event=submission.event,
        nomination_submission=submission,
        dedupe_parts=(submission.pk, 'organizer_email'),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    if submission.event.owner.sms_opt_in:
        sms_notification = queue_sms_notification(
            event_type=Notification.EventType.NOMINATION_SUBMITTED,
            recipient_phone=submission.event.owner.phone_number,
            recipient_name=submission.event.owner.email,
            event=submission.event,
            nomination_submission=submission,
            dedupe_parts=(submission.pk, 'organizer_sms'),
        )
        if sms_notification is not None:
            notifications.append(sms_notification)

    return notifications


def queue_nomination_approved(submission):
    notifications = []
    vote_url = absolute_url(submission.approved_nominee.get_absolute_url()) if submission.approved_nominee_id else ''

    email_notification = queue_notification(
        event_type=Notification.EventType.NOMINATION_APPROVED,
        recipient_email=submission.email,
        recipient_name=submission.name,
        event=submission.event,
        nominee=submission.approved_nominee,
        nomination_submission=submission,
        vote_url=vote_url,
        dedupe_parts=(submission.pk, 'approved_email'),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.NOMINATION_APPROVED,
        recipient_phone=submission.phone_number,
        recipient_name=submission.name,
        event=submission.event,
        nominee=submission.approved_nominee,
        nomination_submission=submission,
        vote_url=vote_url,
        dedupe_parts=(submission.pk, 'approved_sms'),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)

    return notifications


def queue_nomination_rejected(submission):
    notifications = []

    email_notification = queue_notification(
        event_type=Notification.EventType.NOMINATION_REJECTED,
        recipient_email=submission.email,
        recipient_name=submission.name,
        event=submission.event,
        nomination_submission=submission,
        dedupe_parts=(submission.pk, 'rejected_email'),
    )
    if email_notification is not None:
        notifications.append(email_notification)

    sms_notification = queue_sms_notification(
        event_type=Notification.EventType.NOMINATION_REJECTED,
        recipient_phone=submission.phone_number,
        recipient_name=submission.name,
        event=submission.event,
        nomination_submission=submission,
        dedupe_parts=(submission.pk, 'rejected_sms'),
    )
    if sms_notification is not None:
        notifications.append(sms_notification)

    return notifications


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


def queue_voter_turnout_reminders(reference_time=None):
    from events.models import Event
    from elections.models import ElectionCredential
    from elections.models import ElectionVoter

    reference_time = reference_time or timezone.now()
    lead = timedelta(hours=settings.NOTIFICATION_REMINDER_LEAD_HOURS)
    horizon = reference_time + lead

    # Fetch all open secure elections that end within the lead horizon
    ending_elections = Event.objects.filter(
        kind=Event.Kind.SECURE_ELECTION,
        status=Event.Status.OPEN,
        end_at__gt=reference_time,
        end_at__lte=horizon,
    )

    created_reminders = []
    for event in ending_elections:
        # Get all issued credentials (voters who have not yet voted)
        credentials = ElectionCredential.objects.filter(
            event=event,
            status=ElectionCredential.Status.ISSUED,
        ).select_related('voter')

        for cred in credentials:
            voter = cred.voter
            vote_url = absolute_url(reverse('elections:vote', args=[event.slug]))

            if voter.email:
                email_notification = queue_notification(
                    channel=Notification.Channel.EMAIL,
                    event_type=Notification.EventType.VOTER_TURNOUT_REMINDER,
                    recipient_email=voter.email,
                    recipient_name=voter.name,
                    event=event,
                    voter=voter,
                    credential_token="[See original credentials email]",
                    vote_url=vote_url,
                    dedupe_parts=(event.pk, voter.pk, 'turnout_reminder_email'),
                )
                if email_notification:
                    created_reminders.append(email_notification.pk)

            if voter.phone:
                sms_notification = queue_sms_notification(
                    event_type=Notification.EventType.VOTER_TURNOUT_REMINDER,
                    recipient_phone=voter.phone,
                    recipient_name=voter.name,
                    event=event,
                    voter=voter,
                    vote_url=vote_url,
                    dedupe_parts=(event.pk, voter.pk, 'turnout_reminder_sms'),
                )
                if sms_notification:
                    created_reminders.append(sms_notification.pk)

    return created_reminders


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
