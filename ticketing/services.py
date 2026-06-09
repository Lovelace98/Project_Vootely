from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from payments.services import amount_to_minor_units, generate_reference
from wallets.services import post_ticket_ledger_transaction

from .models import Ticket, TicketCheckIn, TicketProvisionalEntry, TicketPurchase, TicketScannerPass, TicketType


def ticket_reference():
    return generate_reference().replace('vc_', 'vct_', 1)


def public_ticket_url(ticket):
    from votecentral.public_urls import build_public_url

    return build_public_url(ticket.get_absolute_url())


def initialize_paystack_ticket_transaction(ticket_purchase):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    email = ticket_purchase.buyer_email
    if not email or '@' not in email or email.endswith('.local'):
        email = 'buyer@vootely.com'

    payload = {
        'reference': ticket_purchase.gateway_reference,
        'amount': amount_to_minor_units(ticket_purchase.amount),
        'currency': ticket_purchase.currency,
        'email': email,
        'callback_url': settings.PAYSTACK_CALLBACK_URL,
        'metadata': {
            'ticket_purchase_id': ticket_purchase.pk,
            'event_slug': ticket_purchase.event.slug,
            'ticket_type_id': ticket_purchase.ticket_type_id,
            'ticket_type_name': ticket_purchase.ticket_type.name,
            'quantity': ticket_purchase.quantity,
            'buyer_name': ticket_purchase.buyer_name,
            'buyer_phone': ticket_purchase.buyer_phone,
            'payment_kind': 'ticket',
        },
    }
    response = requests.post(
        settings.PAYSTACK_INITIALIZE_URL,
        json=payload,
        headers={
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json',
        },
        timeout=15,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get('status'):
        raise RuntimeError(body.get('message') or 'Paystack initialization failed.')
    return body


def charge_ticket_momo_stk_push(ticket_purchase):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    from payments.services import detect_momo_provider

    raw_phone = ticket_purchase.buyer_phone
    phone = raw_phone.strip()
    if phone.startswith('+233'):
        phone = '0' + phone[4:]
    elif phone.startswith('233') and len(phone) == 12:
        phone = '0' + phone[3:]

    provider = detect_momo_provider(phone)
    email = ticket_purchase.buyer_email or f'ussd_{phone}@vootely.com'
    if not email or '@' not in email or email.endswith('.local'):
        email = 'buyer@vootely.com'

    payload = {
        'reference': ticket_purchase.gateway_reference,
        'amount': amount_to_minor_units(ticket_purchase.amount),
        'currency': ticket_purchase.currency,
        'email': email,
        'mobile_money': {
            'phone': phone,
            'provider': provider,
        },
        'metadata': {
            'ticket_purchase_id': ticket_purchase.pk,
            'event_slug': ticket_purchase.event.slug,
            'ticket_type_id': ticket_purchase.ticket_type_id,
            'ticket_type_name': ticket_purchase.ticket_type.name,
            'quantity': ticket_purchase.quantity,
            'buyer_name': ticket_purchase.buyer_name,
            'buyer_phone': ticket_purchase.buyer_phone,
            'payment_kind': 'ticket',
            'is_ussd': True,
        },
    }

    response = requests.post(
        'https://api.paystack.co/charge',
        json=payload,
        headers={
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json',
        },
        timeout=15,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get('status'):
        raise RuntimeError(body.get('message') or 'Paystack STK push charge initialization failed.')
    ticket_purchase.status = TicketPurchase.Status.PENDING
    ticket_purchase.gateway_response = body
    ticket_purchase.save(update_fields=['status', 'gateway_response'])
    return body


def create_ticket_purchase(*, ticket_type, quantity, buyer_name='', buyer_email='', buyer_phone='', ip_address=None, user_agent='', metadata=None):
    allowed, reason = ticket_type.can_purchase(quantity)
    if not allowed:
        raise ValidationError(reason)

    base_amount = (ticket_type.price * Decimal(quantity)).quantize(Decimal('0.01'))
    buyer_handling_fee = (base_amount * Decimal('0.025')).quantize(Decimal('0.01'))
    amount = base_amount + buyer_handling_fee
    return TicketPurchase.objects.create(
        event=ticket_type.event,
        ticket_type=ticket_type,
        amount=amount,
        buyer_handling_fee=buyer_handling_fee,
        currency=ticket_type.event.currency,
        ticket_commission_percent=ticket_type.event.ticket_commission_percent,
        quantity=quantity,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        buyer_phone=buyer_phone,
        ip_address=ip_address,
        user_agent=user_agent,
        gateway_reference=ticket_reference(),
        status=TicketPurchase.Status.INITIALIZED,
        metadata=metadata or {},
    )


def mark_ticket_purchase_unsuccessful(
    ticket_purchase,
    *,
    gateway_status='',
    failure_reason='',
    cancelled=False,
    callback_received=False,
    webhook_payload=None,
):
    if ticket_purchase.status == TicketPurchase.Status.PAID:
        return ticket_purchase

    ticket_purchase.status = (
        TicketPurchase.Status.CANCELLED if cancelled else TicketPurchase.Status.FAILED
    )
    if gateway_status:
        ticket_purchase.gateway_status = gateway_status[:32]
    if failure_reason:
        ticket_purchase.failure_reason = failure_reason[:255]
    if callback_received:
        ticket_purchase.callback_received_at = timezone.now()
    if webhook_payload is not None:
        ticket_purchase.webhook_payload = webhook_payload
        ticket_purchase.confirmed_webhook_at = timezone.now()
    if not ticket_purchase.completed_at:
        ticket_purchase.completed_at = timezone.now()
    ticket_purchase.save(
        update_fields=[
            'status',
            'gateway_status',
            'failure_reason',
            'callback_received_at',
            'webhook_payload',
            'confirmed_webhook_at',
            'completed_at',
        ]
    )
    return ticket_purchase


@transaction.atomic
def record_ticket_paystack_callback(ticket_purchase, callback_status=''):
    callback_status = (callback_status or '').strip().lower()
    ticket_purchase.callback_received_at = timezone.now()
    if callback_status:
        ticket_purchase.gateway_status = callback_status[:32]
    ticket_purchase.save(update_fields=['callback_received_at', 'gateway_status'])
    return ticket_purchase


def issue_tickets(ticket_purchase):
    existing = ticket_purchase.tickets.count()
    if existing >= ticket_purchase.quantity:
        return list(ticket_purchase.tickets.all())

    for _idx in range(ticket_purchase.quantity - existing):
        for _attempt in range(5):
            code = Ticket.generate_unique_code()
            try:
                Ticket.objects.create(
                    purchase=ticket_purchase,
                    ticket_type=ticket_purchase.ticket_type,
                    event=ticket_purchase.event,
                    code=code,
                    qr_data=code,
                    status=Ticket.Status.ACTIVE,
                )
                break
            except IntegrityError:
                if _attempt == 4:
                    raise
    return list(ticket_purchase.tickets.all())


def queue_ticket_notifications(ticket_purchase):
    try:
        from notifications.services import queue_ticket_purchased
    except ImportError:
        return []
    return queue_ticket_purchased(ticket_purchase)


@transaction.atomic
def handle_ticket_paystack_webhook(payload):
    event_name = payload.get('event') or ''
    data = payload.get('data') or {}
    reference = data.get('reference')
    if not reference:
        return None

    purchase = TicketPurchase.objects.select_for_update().select_related(
        'event',
        'event__owner',
        'ticket_type',
    ).get(gateway_reference=reference)
    TicketType.objects.select_for_update().get(pk=purchase.ticket_type_id)

    amount_minor = data.get('amount')
    currency = (data.get('currency') or purchase.currency).upper()
    purchase.webhook_payload = payload
    purchase.confirmed_webhook_at = timezone.now()
    purchase.gateway_status = (data.get('status') or event_name or purchase.gateway_status)[:32]

    if purchase.status == TicketPurchase.Status.PAID:
        purchase.save(update_fields=['webhook_payload', 'confirmed_webhook_at', 'gateway_status'])
        issue_tickets(purchase)
        post_ticket_ledger_transaction(purchase)
        queue_ticket_notifications(purchase)
        return purchase

    gateway_status = (data.get('status') or '').lower()
    failure_reason = data.get('gateway_response') or data.get('message') or payload.get('message') or ''

    if event_name != 'charge.success':
        if gateway_status in {'cancelled', 'abandoned'} or 'cancel' in event_name:
            return mark_ticket_purchase_unsuccessful(
                purchase,
                gateway_status=gateway_status or event_name,
                failure_reason=failure_reason or 'The ticket payment was cancelled before confirmation.',
                cancelled=True,
                webhook_payload=payload,
            )
        if gateway_status in {'failed', 'error'} or 'fail' in event_name:
            return mark_ticket_purchase_unsuccessful(
                purchase,
                gateway_status=gateway_status or event_name,
                failure_reason=failure_reason or 'The ticket payment was not completed successfully.',
                webhook_payload=payload,
            )
        purchase.save(update_fields=['webhook_payload', 'confirmed_webhook_at', 'gateway_status'])
        return purchase

    if amount_minor != amount_to_minor_units(purchase.amount) or currency != purchase.currency:
        return mark_ticket_purchase_unsuccessful(
            purchase,
            gateway_status=gateway_status or 'amount_mismatch',
            failure_reason='The ticket payment amount or currency did not match the expected value.',
            webhook_payload=payload,
        )

    if purchase.quantity > purchase.ticket_type.remaining_quantity:
        return mark_ticket_purchase_unsuccessful(
            purchase,
            gateway_status=gateway_status or 'sold_out',
            failure_reason='This ticket type sold out before payment confirmation.',
            webhook_payload=payload,
        )

    if purchase.event.status in {purchase.event.Status.CANCELLED, purchase.event.Status.ARCHIVED}:
        return mark_ticket_purchase_unsuccessful(
            purchase,
            gateway_status=gateway_status or 'event_unavailable',
            failure_reason='This event is no longer available for ticket sales.',
            webhook_payload=payload,
        )

    purchase.buyer_email = data.get('customer', {}).get('email') or purchase.buyer_email
    purchase.buyer_name = data.get('metadata', {}).get('buyer_name') or purchase.buyer_name
    purchase.buyer_phone = data.get('customer', {}).get('phone') or purchase.buyer_phone
    purchase.status = TicketPurchase.Status.PAID
    purchase.failure_reason = ''
    purchase.gateway_status = (gateway_status or 'success')[:32]
    if not purchase.completed_at:
        purchase.completed_at = timezone.now()
    purchase.save(
        update_fields=[
            'status',
            'webhook_payload',
            'confirmed_webhook_at',
            'completed_at',
            'buyer_email',
            'buyer_name',
            'buyer_phone',
            'gateway_status',
            'failure_reason',
        ]
    )

    issue_tickets(purchase)
    post_ticket_ledger_transaction(purchase)
    queue_ticket_notifications(purchase)
    return purchase


def build_ticket_purchase_status(ticket_purchase):
    if ticket_purchase.status in {
        TicketPurchase.Status.INITIALIZED,
        TicketPurchase.Status.PENDING,
    }:
        return {
            'state': 'pending',
            'title': 'Ticket payment submitted',
            'message': 'Your tickets will be issued after Paystack confirms the payment.',
            'should_poll': True,
            'badge': 'Pending confirmation',
            'variant': 'warning',
            'ticket_purchase': ticket_purchase,
        }
    if ticket_purchase.status == TicketPurchase.Status.PAID:
        return {
            'state': 'paid',
            'title': 'Tickets ready',
            'message': 'Your ticket payment has been confirmed.',
            'should_poll': False,
            'badge': 'Tickets ready',
            'variant': 'success',
            'ticket_purchase': ticket_purchase,
        }
    return {
        'state': 'failed',
        'title': 'Ticket payment unsuccessful',
        'message': 'This ticket payment did not complete successfully.',
        'should_poll': False,
        'badge': 'Payment unsuccessful',
        'variant': 'danger',
        'ticket_purchase': ticket_purchase,
    }


def ticket_purchase_status_redirect_url(ticket_purchase):
    return reverse('ticketing:purchase_detail', args=[ticket_purchase.gateway_reference])


def provisional_result_from_checkin(result):
    message = result.get('message') or ''
    if result.get('ok'):
        return TicketProvisionalEntry.Result.CONFIRMED
    if message == 'This ticket has already been checked in.':
        return TicketProvisionalEntry.Result.DUPLICATE_REJECTED
    if message == 'This ticket belongs to a different event.':
        return TicketProvisionalEntry.Result.WRONG_EVENT_REJECTED
    if message == 'Ticket not found.':
        return TicketProvisionalEntry.Result.NOT_FOUND_REJECTED
    return TicketProvisionalEntry.Result.INACTIVE_REJECTED


def serialize_provisional_entry(entry):
    return {
        'client_attempt_id': entry.client_attempt_id,
        'ticket_code': entry.ticket_code,
        'status': entry.status,
        'result': entry.result,
        'message': entry.message,
        'synced_at': entry.synced_at.isoformat() if entry.synced_at else '',
        'ticket_status': entry.ticket.status if entry.ticket_id else '',
    }


def unauthorized_provisional_result(client_attempt_id='', ticket_code='', message='This provisional attempt does not belong to this scanner.'):
    return {
        'client_attempt_id': client_attempt_id,
        'ticket_code': ticket_code,
        'status': TicketProvisionalEntry.Status.REJECTED,
        'result': TicketProvisionalEntry.Result.UNAUTHORIZED_REJECTED,
        'message': message,
    }


def provisional_attempt_matches_context(entry, *, event, user=None, scanner_pass=None):
    if entry.event_id != event.pk:
        return False
    if isinstance(scanner_pass, TicketScannerPass):
        return entry.scanner_pass_id == scanner_pass.pk
    if getattr(user, 'is_authenticated', False):
        return entry.scanner_pass_id is None and entry.checked_in_by_id == user.pk
    return False


@transaction.atomic
def check_in_ticket(*, event, code, user=None, scanner_pass=None, ip_address=None, user_agent='', scanned_at=None):
    normalized_code = (code or '').strip().upper()
    if not normalized_code:
        return {'ok': False, 'message': 'Enter a ticket code.'}

    try:
        ticket = Ticket.objects.select_for_update().select_related('event', 'ticket_type', 'purchase').get(
            code__iexact=normalized_code
        )
    except Ticket.DoesNotExist:
        return {'ok': False, 'message': 'Ticket not found.'}

    if ticket.event_id != event.id:
        return {'ok': False, 'message': 'This ticket belongs to a different event.'}

    status_before = ticket.status
    ok = False
    if ticket.status == Ticket.Status.USED:
        message = 'This ticket has already been checked in.'
        status_after = ticket.status
    elif ticket.status != Ticket.Status.ACTIVE:
        message = f'This ticket is {ticket.get_status_display().lower()}.'
        status_after = ticket.status
    else:
        ticket.status = Ticket.Status.USED
        ticket.used_at = timezone.now()
        ticket.checked_in_by = user if getattr(user, 'is_authenticated', False) else None
        ticket.save(update_fields=['status', 'used_at', 'checked_in_by'])
        ok = True
        message = f'Checked in {ticket.purchase.buyer_name or ticket.purchase.buyer_email or ticket.code}.'
        status_after = ticket.status

    create_kwargs = {
        'ticket': ticket,
        'event': event,
        'checked_in_by': user if getattr(user, 'is_authenticated', False) else None,
        'scanner_pass': scanner_pass if isinstance(scanner_pass, TicketScannerPass) else None,
        'scanner_gate_name': getattr(scanner_pass, 'gate_name', '') if scanner_pass else '',
        'scanner_staff_label': getattr(scanner_pass, 'staff_label', '') if scanner_pass else '',
        'scanner_ip': ip_address,
        'scanner_user_agent': user_agent,
        'status_before': status_before,
        'status_after': status_after,
        'message': message,
    }
    if scanned_at is not None:
        create_kwargs['scanned_at'] = scanned_at

    checkin = TicketCheckIn.objects.create(**create_kwargs)
    result = {
        'ok': ok,
        'message': message,
        'ticket_code': ticket.code,
        'ticket_status': ticket.status,
        'checkin_id': checkin.pk,
    }
    if ticket.event_id == event.id:
        result.update(
            {
                'buyer_name': ticket.purchase.buyer_name,
                'buyer_email': ticket.purchase.buyer_email,
                'buyer_phone': ticket.purchase.buyer_phone,
                'ticket_type': ticket.ticket_type.name,
                'purchase_reference': ticket.purchase.gateway_reference,
                'used_at': ticket.used_at.isoformat() if ticket.used_at else '',
                'checked_in_by': (
                    ticket.checked_in_by.email
                    if ticket.checked_in_by_id
                    else (getattr(scanner_pass, 'staff_label', '') or getattr(scanner_pass, 'gate_name', '') if scanner_pass else '')
                ),
            }
        )
    return result


@transaction.atomic
def sync_provisional_entry(
    *,
    event,
    attempt,
    user=None,
    scanner_pass=None,
    ip_address=None,
    user_agent='',
):
    client_attempt_id = (attempt.get('client_attempt_id') or '').strip()
    ticket_code = (attempt.get('ticket_code') or attempt.get('code') or '').strip().upper()
    if not client_attempt_id or not ticket_code:
        return {
            'client_attempt_id': client_attempt_id,
            'ticket_code': ticket_code,
            'status': TicketProvisionalEntry.Status.REJECTED,
            'result': TicketProvisionalEntry.Result.INACTIVE_REJECTED,
            'message': 'Missing provisional attempt details.',
        }

    existing = TicketProvisionalEntry.objects.filter(client_attempt_id=client_attempt_id).select_related('ticket').first()
    if existing:
        if not provisional_attempt_matches_context(existing, event=event, user=user, scanner_pass=scanner_pass):
            return unauthorized_provisional_result(
                client_attempt_id=client_attempt_id,
                ticket_code=ticket_code,
                message='This provisional attempt belongs to a different event, scanner, or organizer session.',
            )
        return serialize_provisional_entry(existing)

    offline_at = None
    raw_offline_at = attempt.get('offline_at')
    if raw_offline_at:
        try:
            offline_at = datetime.fromisoformat(str(raw_offline_at).replace('Z', '+00:00'))
            if timezone.is_naive(offline_at):
                offline_at = timezone.make_aware(offline_at)
        except ValueError:
            offline_at = None

    ticket = Ticket.objects.filter(code__iexact=ticket_code).first()
    entry = TicketProvisionalEntry.objects.create(
        event=event,
        ticket=ticket,
        scanner_pass=scanner_pass if isinstance(scanner_pass, TicketScannerPass) else None,
        checked_in_by=user if getattr(user, 'is_authenticated', False) else None,
        client_attempt_id=client_attempt_id,
        ticket_code=ticket_code,
        gate_name=getattr(scanner_pass, 'gate_name', '') if scanner_pass else '',
        staff_label=getattr(scanner_pass, 'staff_label', '') if scanner_pass else '',
        device_id=(attempt.get('device_id') or '')[:80],
        offline_at=offline_at,
        scanner_ip=ip_address,
        scanner_user_agent=user_agent,
        cached_ticket_snapshot=attempt.get('ticket_snapshot') if isinstance(attempt.get('ticket_snapshot'), dict) else {},
    )
    result = check_in_ticket(
        event=event,
        code=ticket_code,
        user=user,
        scanner_pass=scanner_pass,
        ip_address=ip_address,
        user_agent=user_agent,
        scanned_at=offline_at,
    )
    entry.result = provisional_result_from_checkin(result)
    entry.status = (
        TicketProvisionalEntry.Status.CONFIRMED
        if entry.result == TicketProvisionalEntry.Result.CONFIRMED
        else TicketProvisionalEntry.Status.REJECTED
    )
    entry.message = result.get('message', '')[:255]
    entry.synced_at = timezone.now()
    if result.get('ticket_code'):
        entry.ticket = Ticket.objects.filter(code__iexact=result['ticket_code']).first()
    if result.get('ok') and result.get('checkin_id'):
        entry.final_checkin_id = result['checkin_id']
    entry.save(
        update_fields=[
            'ticket',
            'final_checkin',
            'status',
            'result',
            'message',
            'synced_at',
            'updated_at',
        ]
    )
    return serialize_provisional_entry(entry)
