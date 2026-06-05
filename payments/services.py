import hashlib
import hmac
import uuid
from decimal import Decimal

import requests
from django.conf import settings
from django.urls import reverse
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from events.models import Event
from votes.models import VotePurchase
from wallets.services import post_payment_ledger_transaction

from .models import PaymentAttempt


def generate_reference():
    return f'vc_{uuid.uuid4().hex[:20]}'


def amount_to_minor_units(amount):
    return int((amount * Decimal('100')).quantize(Decimal('1')))


def initialize_paystack_transaction(payment_attempt):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    payload = {
        'reference': payment_attempt.gateway_reference,
        'amount': amount_to_minor_units(payment_attempt.amount),
        'currency': payment_attempt.currency,
        'email': payment_attempt.voter_email,
        'callback_url': settings.PAYSTACK_CALLBACK_URL,
        'metadata': {
            'payment_attempt_id': payment_attempt.pk,
            'event_slug': payment_attempt.event.slug,
            'nominee_ref': payment_attempt.nominee.slug,
            'vote_quantity': payment_attempt.vote_quantity,
            'voter_name': payment_attempt.voter_name,
            'voter_phone': payment_attempt.voter_phone,
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


def verify_paystack_signature(raw_body, signature):
    secret = settings.PAYSTACK_WEBHOOK_SECRET or settings.PAYSTACK_SECRET_KEY
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def mark_payment_attempt_unsuccessful(
    payment_attempt,
    *,
    gateway_status='',
    failure_reason='',
    cancelled=False,
    callback_received=False,
    webhook_payload=None,
):
    if payment_attempt.status == PaymentAttempt.Status.PAID:
        return payment_attempt

    payment_attempt.status = (
        PaymentAttempt.Status.CANCELLED if cancelled else PaymentAttempt.Status.FAILED
    )
    if gateway_status:
        payment_attempt.gateway_status = gateway_status
    if failure_reason:
        payment_attempt.failure_reason = failure_reason[:255]
    if callback_received:
        payment_attempt.callback_received_at = timezone.now()
    if webhook_payload is not None:
        payment_attempt.webhook_payload = webhook_payload
        payment_attempt.confirmed_webhook_at = timezone.now()
    if not payment_attempt.completed_at:
        payment_attempt.completed_at = timezone.now()
    payment_attempt.save(
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
    return payment_attempt


@transaction.atomic
def record_paystack_callback(payment_attempt, callback_status=''):
    callback_status = (callback_status or '').strip().lower()
    payment_attempt.callback_received_at = timezone.now()
    if callback_status:
        payment_attempt.gateway_status = callback_status[:32]

    payment_attempt.save(
        update_fields=[
            'callback_received_at',
            'gateway_status',
        ]
    )
    return payment_attempt


def build_public_payment_status(payment_attempt):
    if payment_attempt.status in {
        PaymentAttempt.Status.INITIALIZED,
        PaymentAttempt.Status.PENDING,
    }:
        return {
            'state': 'pending',
            'title': 'Payment submitted',
            'message': 'Paystack has received your payment request. Your votes will count after confirmation arrives.',
            'should_poll': True,
            'badge': 'Pending confirmation',
            'variant': 'warning',
            'payment_attempt': payment_attempt,
        }

    if payment_attempt.status == PaymentAttempt.Status.PAID:
        return {
            'state': 'paid',
            'title': 'Vote counted',
            'message': 'Your payment has been confirmed and the votes have been added to this nominee.',
            'should_poll': False,
            'badge': 'Vote counted',
            'variant': 'success',
            'payment_attempt': payment_attempt,
        }

    return {
        'state': 'failed',
        'title': 'Payment unsuccessful',
        'message': 'This payment did not complete successfully, so no votes were added. You can try again.',
        'should_poll': False,
        'badge': 'Payment unsuccessful',
        'variant': 'danger',
        'payment_attempt': payment_attempt,
    }


def payment_reference_not_found(message):
    return {
        'state': 'not_found',
        'title': 'Payment reference not found',
        'message': message,
        'should_poll': False,
        'badge': 'Reference not found',
        'variant': 'warning',
    }


def event_has_public_payment_context(event):
    return event.is_public and event.status in {
        Event.Status.PUBLISHED,
        Event.Status.CLOSED,
    }


def resolve_public_payment_status(event, nominee, reference):
    if not reference:
        return None

    try:
        payment_attempt = PaymentAttempt.objects.get(
            gateway_reference=reference,
            event=event,
            nominee=nominee,
        )
    except PaymentAttempt.DoesNotExist:
        return payment_reference_not_found(
            'We could not find a payment for this nominee using that reference.'
        )

    return build_public_payment_status(payment_attempt)


def resolve_payment_status_by_reference(reference):
    if not reference:
        return None

    try:
        payment_attempt = PaymentAttempt.objects.select_related('event', 'nominee').get(
            gateway_reference=reference
        )
    except PaymentAttempt.DoesNotExist:
        return payment_reference_not_found(
            'We could not find a Vootely payment using that reference.'
        )

    payment_status = build_public_payment_status(payment_attempt)
    payment_status['show_payment_details'] = event_has_public_payment_context(
        payment_attempt.event
    )
    return payment_status


def payment_status_redirect_url(payment_attempt):
    if event_has_public_payment_context(payment_attempt.event):
        return f'{payment_attempt.nominee.get_absolute_url()}?payment_reference={payment_attempt.gateway_reference}'
    return reverse('payments:status_detail', args=[payment_attempt.gateway_reference])


def payment_attempt_has_commission(payment_attempt):
    return payment_attempt.platform_commission_percent is not None


def verify_and_process_paystack_payment(reference):
    if not settings.PAYSTACK_SECRET_KEY:
        return None

    try:
        response = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={
                'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    body = response.json()
    if not body.get('status') or not body.get('data'):
        return None

    data = body['data']
    status = data.get('status')
    if status == 'success':
        payload = {'event': 'charge.success', 'data': data}
        
        # Check if it is a standard payment attempt
        try:
            attempt = PaymentAttempt.objects.get(gateway_reference=reference)
            if attempt.status != PaymentAttempt.Status.PAID:
                handle_paystack_webhook(payload)
                return 'vote_success'
        except PaymentAttempt.DoesNotExist:
            pass

        # Check if it is an organizer payment attempt
        try:
            from elections.models import OrganizerPaymentAttempt
            from elections.services import handle_organizer_paystack_webhook
            
            attempt = OrganizerPaymentAttempt.objects.get(gateway_reference=reference)
            if attempt.status != OrganizerPaymentAttempt.Status.PAID:
                handle_organizer_paystack_webhook(payload)
                return 'organizer_success'
        except (OrganizerPaymentAttempt.DoesNotExist, ImportError):
            pass
    elif status in {'abandoned', 'failed', 'reversed'}:
        # Check standard payment attempt
        try:
            attempt = PaymentAttempt.objects.get(gateway_reference=reference)
            if attempt.status not in {PaymentAttempt.Status.PAID, PaymentAttempt.Status.FAILED, PaymentAttempt.Status.CANCELLED}:
                mark_payment_attempt_unsuccessful(
                    attempt,
                    gateway_status=status,
                    failure_reason=data.get('gateway_response') or 'The payment was not completed successfully.',
                    cancelled=(status == 'abandoned'),
                )
                return 'vote_failed'
        except PaymentAttempt.DoesNotExist:
            pass

        # Check organizer payment attempt
        try:
            from elections.models import OrganizerPaymentAttempt
            from elections.services import mark_organizer_attempt_unsuccessful
            
            attempt = OrganizerPaymentAttempt.objects.get(gateway_reference=reference)
            if attempt.status not in {OrganizerPaymentAttempt.Status.PAID, OrganizerPaymentAttempt.Status.FAILED, OrganizerPaymentAttempt.Status.CANCELLED}:
                mark_organizer_attempt_unsuccessful(
                    attempt,
                    gateway_status=status,
                    failure_reason=data.get('gateway_response') or 'The payment was not completed successfully.',
                    cancelled=(status == 'abandoned'),
                )
                return 'organizer_failed'
        except (OrganizerPaymentAttempt.DoesNotExist, ImportError):
            pass

    return None


@transaction.atomic
def handle_paystack_webhook(payload):
    event_name = payload.get('event')
    data = payload.get('data') or {}
    reference = data.get('reference')
    if not reference:
        return None

    attempt = PaymentAttempt.objects.select_for_update().select_related(
        'event',
        'nominee',
        'event__owner',
    ).get(gateway_reference=reference)

    amount_minor = data.get('amount')
    currency = (data.get('currency') or attempt.currency).upper()
    attempt.webhook_payload = payload
    attempt.confirmed_webhook_at = timezone.now()
    attempt.gateway_status = (data.get('status') or event_name or attempt.gateway_status)[:32]

    if attempt.status == PaymentAttempt.Status.PAID and hasattr(attempt, 'vote_purchase'):
        attempt.save(
            update_fields=['webhook_payload', 'confirmed_webhook_at', 'gateway_status']
        )
        post_payment_ledger_transaction(attempt)
        from notifications.services import queue_payment_confirmed

        queue_payment_confirmed(attempt)
        return attempt

    gateway_status = (data.get('status') or '').lower()
    failure_reason = (
        data.get('gateway_response')
        or data.get('message')
        or payload.get('message')
        or ''
    )

    if event_name != 'charge.success':
        if gateway_status in {'cancelled', 'abandoned'} or 'cancel' in event_name:
            attempt = mark_payment_attempt_unsuccessful(
                attempt,
                gateway_status=gateway_status or event_name,
                failure_reason=failure_reason or 'The payment was cancelled before confirmation.',
                cancelled=True,
                webhook_payload=payload,
            )
            from notifications.services import queue_payment_cancelled

            queue_payment_cancelled(attempt)
            return attempt
        if gateway_status in {'failed', 'error'} or 'fail' in event_name:
            attempt = mark_payment_attempt_unsuccessful(
                attempt,
                gateway_status=gateway_status or event_name,
                failure_reason=failure_reason or 'The payment was not completed successfully.',
                webhook_payload=payload,
            )
            from notifications.services import queue_payment_failed

            queue_payment_failed(attempt)
            return attempt
        attempt.save(update_fields=['webhook_payload', 'confirmed_webhook_at', 'gateway_status'])
        return attempt

    if amount_minor != amount_to_minor_units(attempt.amount) or currency != attempt.currency:
        attempt = mark_payment_attempt_unsuccessful(
            attempt,
            gateway_status=gateway_status or 'amount_mismatch',
            failure_reason='The payment amount or currency did not match the expected value.',
            webhook_payload=payload,
        )
        from notifications.services import queue_payment_failed

        queue_payment_failed(attempt)
        return attempt

    if not payment_attempt_has_commission(attempt):
        attempt = mark_payment_attempt_unsuccessful(
            attempt,
            gateway_status=gateway_status or 'commission_unset',
            failure_reason='This event is not ready to accept votes yet.',
            webhook_payload=payload,
        )
        from notifications.services import queue_payment_failed

        queue_payment_failed(attempt)
        return attempt

    if not attempt.event.accepts_votes():
        attempt = mark_payment_attempt_unsuccessful(
            attempt,
            gateway_status=gateway_status or 'event_unavailable',
            failure_reason='This event is no longer accepting votes.',
            webhook_payload=payload,
        )
        from notifications.services import queue_payment_failed

        queue_payment_failed(attempt)
        return attempt

    attempt.voter_email = data.get('customer', {}).get('email') or attempt.voter_email
    attempt.voter_name = data.get('metadata', {}).get('voter_name') or attempt.voter_name
    attempt.voter_phone = data.get('customer', {}).get('phone') or attempt.voter_phone
    attempt.status = PaymentAttempt.Status.PAID
    attempt.failure_reason = ''
    attempt.gateway_status = (gateway_status or 'success')[:32]
    if not attempt.completed_at:
        attempt.completed_at = timezone.now()
    attempt.save(
        update_fields=[
            'status',
            'webhook_payload',
            'confirmed_webhook_at',
            'completed_at',
            'voter_email',
            'voter_name',
            'voter_phone',
            'gateway_status',
            'failure_reason',
        ]
    )

    VotePurchase.objects.get_or_create(
        payment_attempt=attempt,
        defaults={
            'event': attempt.event,
            'nominee': attempt.nominee,
            'quantity': attempt.vote_quantity,
            'amount_paid': attempt.amount,
            'currency': attempt.currency,
            'payment_reference': attempt.gateway_reference,
            'voter_name': attempt.voter_name,
            'voter_email': attempt.voter_email,
            'voter_phone': attempt.voter_phone,
            'ip_address': attempt.ip_address,
            'user_agent': attempt.user_agent,
            'paid_at': attempt.completed_at or timezone.now(),
            'metadata': payload,
        },
    )
    post_payment_ledger_transaction(attempt)
    from notifications.services import queue_payment_confirmed

    queue_payment_confirmed(attempt)
    return attempt


def get_paystack_banks(country='ghana', type=None):
    if not settings.PAYSTACK_SECRET_KEY:
        return []

    from django.core.cache import cache
    cache_key = f'paystack_banks_{country}_{type or "all"}'
    cached_banks = cache.get(cache_key)
    if cached_banks:
        return cached_banks

    params = {'country': country}
    if type:
        params['type'] = type

    try:
        response = requests.get(
            'https://api.paystack.co/bank',
            params=params,
            headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'},
            timeout=10,
        )
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    
    banks = response.json().get('data') or []
    cache.set(cache_key, banks, 86400)  # Cache for 24 hours
    return banks


def resolve_paystack_account(account_number, bank_code):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    try:
        response = requests.get(
            'https://api.paystack.co/bank/resolve',
            params={'account_number': account_number, 'bank_code': bank_code},
            headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'},
            timeout=10,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError('Could not verify the payout account right now. Please try again.') from exc
    if not body.get('status'):
        raise ValidationError(body.get('message') or 'Could not resolve account.')
    return body.get('data')


def detect_momo_provider(phone):
    digits = ''.join(c for c in phone if c.isdigit())
    if digits.startswith('233'):
        local = '0' + digits[3:]
    else:
        local = digits
        if not local.startswith('0') and len(local) == 9:
            local = '0' + local

    if len(local) < 3:
        return 'mtn'

    prefix3 = local[:3]
    
    mtn_prefixes = {'024', '054', '055', '059', '053', '025'}
    telecel_prefixes = {'020', '050'}
    tigo_prefixes = {'026', '056', '027', '057'}

    if prefix3 in mtn_prefixes:
        return 'mtn'
    elif prefix3 in telecel_prefixes:
        return 'vodafone'  # Paystack uses 'vodafone' internally for Telecel
    elif prefix3 in tigo_prefixes:
        return 'tigo'      # Paystack uses 'tigo' internally for AirtelTigo

    return 'mtn'


def charge_momo_stk_push(payment_attempt):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    raw_phone = payment_attempt.voter_phone
    phone = raw_phone.strip()
    if phone.startswith('+233'):
        phone = '0' + phone[4:]
    elif phone.startswith('233') and len(phone) == 12:
        phone = '0' + phone[3:]

    provider = detect_momo_provider(phone)
    
    payload = {
        'reference': payment_attempt.gateway_reference,
        'amount': amount_to_minor_units(payment_attempt.amount),
        'currency': payment_attempt.currency,  # GHS
        'email': payment_attempt.voter_email or 'ussd-voter@vootely.com',
        'mobile_money': {
            'phone': phone,
            'provider': provider
        },
        'metadata': {
            'payment_attempt_id': payment_attempt.pk,
            'event_slug': payment_attempt.event.slug,
            'nominee_ref': payment_attempt.nominee.slug,
            'vote_quantity': payment_attempt.vote_quantity,
            'voter_name': payment_attempt.voter_name,
            'voter_phone': payment_attempt.voter_phone,
            'is_ussd': True
        }
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

    # Update payment attempt status to pending
    payment_attempt.status = PaymentAttempt.Status.PENDING
    payment_attempt.save(update_fields=['status'])
    return body

