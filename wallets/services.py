from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.db import IntegrityError, transaction

from .models import LedgerEntry, LedgerTransaction, WalletAccount, WithdrawalRequest


WITHDRAWAL_RESERVED_STATUSES = (
    WithdrawalRequest.Status.PENDING,
    WithdrawalRequest.Status.APPROVED,
    WithdrawalRequest.Status.PROCESSING,
    WithdrawalRequest.Status.COMPLETED,
)

WITHDRAWAL_IN_PROGRESS_STATUSES = (
    WithdrawalRequest.Status.APPROVED,
    WithdrawalRequest.Status.PROCESSING,
)


def quantize_amount(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def get_platform_account():
    account, _ = WalletAccount.objects.get_or_create(
        kind=WalletAccount.Kind.PLATFORM,
        owner=None,
        defaults={
            'code': 'platform',
            'name': 'Vootely Platform',
        },
    )
    return account


def get_organizer_account(user):
    account, _ = WalletAccount.objects.get_or_create(
        owner=user,
        defaults={
            'kind': WalletAccount.Kind.ORGANIZER,
            'code': f'organizer-{user.pk}',
            'name': user.email,
        },
    )
    return account


def aggregate_money(queryset, field_name='amount'):
    return queryset.aggregate(
        total=Coalesce(
            Sum(field_name),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    )['total']


def get_confirmed_earnings_total(user):
    return aggregate_money(
        LedgerEntry.objects.filter(
            account__owner=user,
            kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
        )
    )


def get_completed_withdrawal_total(user):
    return aggregate_money(
        WithdrawalRequest.objects.filter(
            organizer=user,
            status=WithdrawalRequest.Status.COMPLETED,
        )
    )


def get_in_progress_withdrawal_total(user):
    return aggregate_money(
        WithdrawalRequest.objects.filter(
            organizer=user,
            status__in=WITHDRAWAL_IN_PROGRESS_STATUSES,
        )
    )


def get_pending_review_withdrawal_total(user):
    return aggregate_money(
        WithdrawalRequest.objects.filter(
            organizer=user,
            status=WithdrawalRequest.Status.PENDING,
        )
    )


def get_reserved_withdrawal_total(user, *, exclude_withdrawal=None, exclude_pending=False):
    statuses = list(WITHDRAWAL_RESERVED_STATUSES)
    if exclude_pending:
        statuses = [s for s in statuses if s != WithdrawalRequest.Status.PENDING]
    queryset = WithdrawalRequest.objects.filter(
        organizer=user,
        status__in=statuses,
    )
    if exclude_withdrawal is not None and exclude_withdrawal.pk:
        queryset = queryset.exclude(pk=exclude_withdrawal.pk)
    return aggregate_money(queryset)


def get_available_withdrawal_balance(user, *, exclude_withdrawal=None, exclude_pending=False):
    confirmed_earnings = get_confirmed_earnings_total(user)
    reserved_total = get_reserved_withdrawal_total(
        user,
        exclude_withdrawal=exclude_withdrawal,
        exclude_pending=exclude_pending,
    )
    return quantize_amount(max(confirmed_earnings - reserved_total, Decimal('0.00')))


def get_withdrawal_dashboard_summary(user):
    confirmed_earnings = get_confirmed_earnings_total(user)
    total_withdrawn = get_completed_withdrawal_total(user)
    in_progress_total = get_in_progress_withdrawal_total(user)
    pending_review_total = get_pending_review_withdrawal_total(user)
    return {
        'confirmed_earnings': confirmed_earnings,
        'available_to_withdraw': get_available_withdrawal_balance(user),
        'total_withdrawn': total_withdrawn,
        'in_progress_total': in_progress_total,
        'pending_review_total': pending_review_total,
    }


def validate_withdrawal_amount(user, amount, *, exclude_withdrawal=None):
    amount = quantize_amount(amount)
    available_balance = get_available_withdrawal_balance(
        user,
        exclude_withdrawal=exclude_withdrawal,
    )
    if amount > available_balance:
        raise ValidationError(
            f'This withdrawal exceeds the available balance of {available_balance:.2f}.'
        )
    return amount


@transaction.atomic
def post_payment_ledger_transaction(payment_attempt):
    if hasattr(payment_attempt, 'ledger_transaction'):
        return payment_attempt.ledger_transaction

    organizer_account = get_organizer_account(payment_attempt.event.owner)
    platform_account = get_platform_account()
    gross = quantize_amount(payment_attempt.amount)
    if payment_attempt.platform_commission_percent is None:
        raise ValidationError('Payment attempt is missing a platform commission snapshot.')

    commission_rate = (
        Decimal(payment_attempt.platform_commission_percent) / Decimal('100')
    ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    commission = quantize_amount(gross * commission_rate)
    organizer_share = quantize_amount(gross - commission)

    transaction_row = LedgerTransaction.objects.create(
        reference=payment_attempt.gateway_reference,
        payment_attempt=payment_attempt,
        description=f'Vote purchase by {payment_attempt.voter_name or payment_attempt.voter_email or "Guest"} for {payment_attempt.event.title}',
        metadata={
            'event_id': payment_attempt.event_id,
            'nominee_id': payment_attempt.nominee_id,
            'vote_quantity': payment_attempt.vote_quantity,
            'voter_name': payment_attempt.voter_name,
            'voter_email': payment_attempt.voter_email,
            'voter_phone': payment_attempt.voter_phone,
            'commission_percent': str(payment_attempt.platform_commission_percent),
            'commission_amount': str(commission),
            'organizer_share_amount': str(organizer_share),
            'gross_amount': str(gross),
        },
    )
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                transaction=transaction_row,
                account=organizer_account,
                amount=organizer_share,
                kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            ),
            LedgerEntry(
                transaction=transaction_row,
                account=platform_account,
                amount=commission,
                kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT,
            ),
            LedgerEntry(
                transaction=transaction_row,
                account=platform_account,
                amount=-gross,
                kind=LedgerEntry.Kind.GATEWAY_SETTLEMENT_DEBIT,
            ),
        ]
    )
    transaction_row.ensure_balanced()
    from events.performance import bump_event_cache, bump_organizer_cache

    bump_organizer_cache(payment_attempt.event.owner_id)
    bump_event_cache(payment_attempt.event_id)
    return transaction_row


@transaction.atomic
def post_ticket_ledger_transaction(ticket_purchase):
    if hasattr(ticket_purchase, 'ledger_transaction'):
        return ticket_purchase.ledger_transaction

    organizer_account = get_organizer_account(ticket_purchase.event.owner)
    platform_account = get_platform_account()
    gross = quantize_amount(ticket_purchase.amount)
    base_amount = quantize_amount(ticket_purchase.amount - ticket_purchase.buyer_handling_fee)
    commission_rate = (
        Decimal(ticket_purchase.ticket_commission_percent) / Decimal('100')
    ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    commission = quantize_amount(base_amount * commission_rate)
    organizer_share = quantize_amount(base_amount - commission)
    platform_fee = quantize_amount(commission + ticket_purchase.buyer_handling_fee)

    try:
        with transaction.atomic():
            transaction_row = LedgerTransaction.objects.create(
                reference=ticket_purchase.gateway_reference,
                ticket_purchase=ticket_purchase,
                description=f'Ticket purchase by {ticket_purchase.buyer_name or ticket_purchase.buyer_email or "Guest"} for {ticket_purchase.event.title}',
                metadata={
                    'event_id': ticket_purchase.event_id,
                    'ticket_type_id': ticket_purchase.ticket_type_id,
                    'ticket_type_name': ticket_purchase.ticket_type.name,
                    'quantity': ticket_purchase.quantity,
                    'buyer_name': ticket_purchase.buyer_name,
                    'buyer_email': ticket_purchase.buyer_email,
                    'buyer_phone': ticket_purchase.buyer_phone,
                    'commission_percent': str(ticket_purchase.ticket_commission_percent),
                    'commission_amount': str(commission),
                    'buyer_handling_fee': str(ticket_purchase.buyer_handling_fee),
                    'organizer_share_amount': str(organizer_share),
                    'gross_amount': str(gross),
                    'revenue_type': 'ticket',
                },
            )
    except IntegrityError:
        return LedgerTransaction.objects.get(ticket_purchase=ticket_purchase)
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                transaction=transaction_row,
                account=organizer_account,
                amount=organizer_share,
                kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            ),
            LedgerEntry(
                transaction=transaction_row,
                account=platform_account,
                amount=platform_fee,
                kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT,
            ),
            LedgerEntry(
                transaction=transaction_row,
                account=platform_account,
                amount=-gross,
                kind=LedgerEntry.Kind.GATEWAY_SETTLEMENT_DEBIT,
            ),
        ]
    )
    transaction_row.ensure_balanced()
    from events.performance import bump_event_cache, bump_organizer_cache

    bump_organizer_cache(ticket_purchase.event.owner_id)
    bump_event_cache(ticket_purchase.event_id)
    return transaction_row


@transaction.atomic
def post_withdrawal_ledger_transaction(withdrawal_request):
    try:
        return withdrawal_request.ledger_transaction
    except LedgerTransaction.DoesNotExist:
        pass

    organizer_account = withdrawal_request.wallet_account
    platform_account = get_platform_account()
    amount = quantize_amount(withdrawal_request.amount)

    transaction_row = LedgerTransaction.objects.create(
        reference=f'withdrawal-{withdrawal_request.pk}',
        withdrawal_request=withdrawal_request,
        description=f'Organizer withdrawal for {withdrawal_request.organizer.email}',
        metadata={
            'organizer_id': withdrawal_request.organizer_id,
            'bank_name': withdrawal_request.bank_name,
            'payout_reference': withdrawal_request.payout_reference,
        },
    )
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                transaction=transaction_row,
                account=organizer_account,
                amount=-amount,
                kind=LedgerEntry.Kind.ORGANIZER_WITHDRAWAL_DEBIT,
            ),
            LedgerEntry(
                transaction=transaction_row,
                account=platform_account,
                amount=amount,
                kind=LedgerEntry.Kind.PLATFORM_PAYOUT_CREDIT,
            ),
        ]
    )
    transaction_row.ensure_balanced()
    from events.performance import bump_organizer_cache

    bump_organizer_cache(withdrawal_request.organizer_id)
    return transaction_row
