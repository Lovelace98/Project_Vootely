from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DecimalField, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from elections.models import (
    Ballot,
    ElectionCredential,
    ElectionVoter,
    OrganizerPaymentAttempt,
)
from events.models import Event
from notifications.models import Notification
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.models import LedgerEntry, LedgerTransaction, WithdrawalRequest


TIMEFRAME_LABELS = {
    'today': 'Today',
    'this_week': 'This week',
    'this_month': 'This month',
    'this_year': 'This year',
    'all_time': 'All time',
}


def environment_callback(request):
    if settings.DEBUG:
        return ['Development', 'warning']
    return ['Production', 'success']


def _timeframe_start(value):
    now = timezone.now()
    value = value if value in TIMEFRAME_LABELS else 'this_month'
    if value == 'today':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if value == 'this_week':
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if value == 'this_month':
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if value == 'this_year':
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _money(queryset, field='amount'):
    return queryset.aggregate(
        total=Coalesce(
            Sum(field),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )['total']


def _count(queryset, field='id'):
    return queryset.aggregate(
        total=Coalesce(Count(field), Value(0), output_field=IntegerField())
    )['total']


def _admin_url(name, obj):
    return reverse(name, args=[obj.pk])


def _scoped_events(kind):
    queryset = Event.objects.all()
    if kind in {Event.Kind.PAID_COMPETITION, Event.Kind.SECURE_ELECTION}:
        queryset = queryset.filter(kind=kind)
    return queryset


def build_dashboard_context(request):
    timeframe = request.GET.get('timeframe', 'this_month')
    if timeframe not in TIMEFRAME_LABELS:
        timeframe = 'this_month'
    kind = request.GET.get('kind', 'all')
    if kind not in {'all', Event.Kind.PAID_COMPETITION, Event.Kind.SECURE_ELECTION}:
        kind = 'all'

    start = _timeframe_start(timeframe)
    events = _scoped_events(kind)
    event_filter = Q(event__kind=kind) if kind != 'all' else Q()

    vote_purchases = VotePurchase.objects.filter(event_filter)
    ballots = Ballot.objects.filter(event_filter)
    payments = PaymentAttempt.objects.filter(event_filter)
    organizer_payments = OrganizerPaymentAttempt.objects.filter(event_filter)
    ledger_entries = LedgerEntry.objects.all()
    withdrawals = WithdrawalRequest.objects.all()
    notifications = Notification.objects.all()

    if start:
        events = events.filter(created_at__gte=start)
        vote_purchases = vote_purchases.filter(paid_at__gte=start)
        ballots = ballots.filter(cast_at__gte=start)
        payments = payments.filter(initiated_at__gte=start)
        organizer_payments = organizer_payments.filter(initiated_at__gte=start)
        ledger_entries = ledger_entries.filter(created_at__gte=start)
        withdrawals = withdrawals.filter(requested_at__gte=start)
        notifications = notifications.filter(queued_at__gte=start)

    confirmed_votes = vote_purchases.aggregate(
        total=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField())
    )['total']
    gross_vote_revenue = _money(vote_purchases, 'amount_paid')
    net_organizer_credits = _money(
        ledger_entries.filter(kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT)
    )
    platform_fee_credits = _money(
        ledger_entries.filter(kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT)
    )

    eligible_voters = ElectionVoter.objects.filter(status=ElectionVoter.Status.ELIGIBLE)
    used_credentials = ElectionCredential.objects.filter(status=ElectionCredential.Status.USED)
    if kind == Event.Kind.SECURE_ELECTION:
        eligible_voters = eligible_voters.filter(event__kind=kind)
        used_credentials = used_credentials.filter(event__kind=kind)
    if start:
        eligible_voters = eligible_voters.filter(created_at__gte=start)
        used_credentials = used_credentials.filter(used_at__gte=start)
    eligible_count = _count(eligible_voters)
    used_count = _count(used_credentials)
    turnout_rate = round((used_count / eligible_count) * 100, 1) if eligible_count else 0

    pending_statuses = [PaymentAttempt.Status.INITIALIZED, PaymentAttempt.Status.PENDING]
    failed_statuses = [PaymentAttempt.Status.FAILED, PaymentAttempt.Status.CANCELLED]
    pending_payments = _count(payments.filter(status__in=pending_statuses)) + _count(
        organizer_payments.filter(status__in=pending_statuses)
    )
    failed_payments = _count(payments.filter(status__in=failed_statuses)) + _count(
        organizer_payments.filter(status__in=failed_statuses)
    )

    ledger_imbalance_qs = LedgerTransaction.objects.annotate(
        entry_total=Coalesce(
            Sum('entries__amount'),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    ).exclude(entry_total=Decimal('0.00'))

    recent_payments = [
        {
            'label': payment.gateway_reference,
            'description': f'{payment.event.title} - {payment.amount} {payment.currency}',
            'status': payment.status,
            'url': _admin_url('admin:payments_paymentattempt_change', payment),
        }
        for payment in PaymentAttempt.objects.select_related('event')
        .filter(event_filter)
        .order_by('-initiated_at')[:6]
    ]
    withdrawal_queue = [
        {
            'label': withdrawal.organizer.email,
            'description': f'{withdrawal.amount} {withdrawal.currency} - {withdrawal.payout_name}',
            'status': withdrawal.status,
            'url': _admin_url('admin:wallets_withdrawalrequest_change', withdrawal),
        }
        for withdrawal in WithdrawalRequest.objects.select_related('organizer')
        .filter(status=WithdrawalRequest.Status.PENDING)
        .order_by('-requested_at')[:6]
    ]
    election_queue = [
        {
            'label': event.title,
            'description': event.get_kind_display(),
            'status': event.status,
            'url': _admin_url('admin:events_event_change', event),
        }
        for event in Event.objects.filter(kind=Event.Kind.SECURE_ELECTION)
        .exclude(status__in=[Event.Status.CERTIFIED, Event.Status.ARCHIVED, Event.Status.CANCELLED])
        .order_by('-updated_at')[:6]
    ]
    failed_notifications = [
        {
            'label': notification.get_event_type_display(),
            'description': notification.recipient_email or notification.recipient_phone or 'unknown recipient',
            'status': notification.status,
            'url': _admin_url('admin:notifications_notification_change', notification),
        }
        for notification in Notification.objects.filter(status=Notification.Status.FAILED)
        .order_by('-last_attempt_at', '-queued_at')[:6]
    ]
    ledger_alerts = [
        {
            'label': transaction.reference,
            'description': f'Imbalance: {transaction.entry_total}',
            'status': 'danger',
            'url': _admin_url('admin:wallets_ledgertransaction_change', transaction),
        }
        for transaction in ledger_imbalance_qs.order_by('-posted_at')[:6]
    ]

    return {
        'admin_timeframe': timeframe,
        'admin_kind': kind,
        'timeframe_options': TIMEFRAME_LABELS.items(),
        'kind_options': [
            ('all', 'All event types'),
            (Event.Kind.PAID_COMPETITION, 'Paid competitions'),
            (Event.Kind.SECURE_ELECTION, 'Secure elections'),
        ],
        'kpi_cards': [
            {'label': 'Organizers', 'value': User.objects.filter(is_staff=False).count(), 'hint': 'Non-staff accounts'},
            {'label': 'Paid competitions', 'value': Event.objects.filter(kind=Event.Kind.PAID_COMPETITION).count(), 'hint': 'All time'},
            {'label': 'Secure elections', 'value': Event.objects.filter(kind=Event.Kind.SECURE_ELECTION).count(), 'hint': 'All time'},
            {'label': 'Live/open events', 'value': Event.objects.filter(status__in=[Event.Status.PUBLISHED, Event.Status.OPEN]).count(), 'hint': 'Accepting public activity'},
            {'label': 'Closed events', 'value': Event.objects.filter(status__in=[Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED]).count(), 'hint': 'Closed or finalized'},
            {'label': 'Confirmed votes', 'value': confirmed_votes, 'hint': TIMEFRAME_LABELS[timeframe]},
            {'label': 'Gross vote revenue', 'value': gross_vote_revenue, 'hint': 'Successful vote purchases'},
            {'label': 'Net organizer credits', 'value': net_organizer_credits, 'hint': 'Ledger-backed'},
            {'label': 'Platform fees', 'value': platform_fee_credits, 'hint': 'Ledger-backed'},
            {'label': 'Ballots cast', 'value': ballots.count(), 'hint': 'Secure elections'},
            {'label': 'Turnout', 'value': f'{turnout_rate}%', 'hint': f'{used_count} of {eligible_count} credentials used'},
            {'label': 'Pending payments', 'value': pending_payments, 'hint': 'Vote and invoice attempts'},
            {'label': 'Failed payments', 'value': failed_payments, 'hint': 'Needs reconciliation'},
            {'label': 'Pending withdrawals', 'value': withdrawals.filter(status=WithdrawalRequest.Status.PENDING).count(), 'hint': 'Awaiting review'},
            {'label': 'Notification failures', 'value': notifications.filter(status=Notification.Status.FAILED).count(), 'hint': 'Retry candidates'},
            {'label': 'Ledger imbalances', 'value': ledger_imbalance_qs.count(), 'hint': 'Should be zero'},
        ],
        'recent_payments': recent_payments,
        'withdrawal_queue': withdrawal_queue,
        'election_queue': election_queue,
        'failed_notifications': failed_notifications,
        'ledger_alerts': ledger_alerts,
    }


def dashboard_callback(request, context):
    context.update(build_dashboard_context(request))
    return context
