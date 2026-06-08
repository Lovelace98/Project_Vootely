from __future__ import annotations

import datetime
from decimal import Decimal

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.db.models import Count, DecimalField, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce, ExtractMonth, ExtractWeekDay
from django.utils import timezone

from elections.models import BallotSelection
from events.models import Event
from nominees.models import Nominee
from payments.models import PaymentAttempt
from ticketing.models import TicketPurchase
from votes.models import VotePurchase
from wallets.models import LedgerEntry, LedgerTransaction, WithdrawalRequest
from wallets.services import (
    WITHDRAWAL_IN_PROGRESS_STATUSES,
    get_available_withdrawal_balance,
    get_withdrawal_dashboard_summary,
)


DASHBOARD_CACHE_TTL = 30
LEADERBOARD_CACHE_TTL = 15
NOTIFICATION_CACHE_TTL = 10


def broadcast_leaderboard_update(event_id):
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'leaderboard_{event_id}',
        {'type': 'leaderboard_updated'},
    )


def broadcast_election_tally_update(event_id):
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'election_tally_{event_id}',
        {'type': 'tally_updated'},
    )


def _version_key(kind, identifier):
    return f'vcache:v1:{kind}:{identifier}:version'


def get_cache_version(kind, identifier):
    key = _version_key(kind, identifier)
    version = cache.get(key)
    if version is None:
        cache.set(key, 1, None)
        return 1
    return version


def bump_cache_version(kind, identifier):
    key = _version_key(kind, identifier)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 2, None)
        return 2


def bump_organizer_cache(user_id):
    if user_id:
        bump_cache_version('organizer', user_id)


def bump_event_cache(event_id):
    if event_id:
        bump_cache_version('event', event_id)


def bump_notification_cache(user_id):
    if user_id:
        bump_cache_version('notifications', user_id)


def _cache_get_or_set(key, builder, ttl):
    value = cache.get(key)
    if value is not None:
        return value
    value = builder()
    cache.set(key, value, ttl)
    return value


def normalize_timeframe(value):
    value = (value or 'this_month').strip().lower()
    return value if value in {'today', 'this_week', 'this_month', 'this_year', 'all_time'} else 'this_month'


def timeframe_start(timeframe, now=None):
    now = now or timezone.now()
    timeframe = normalize_timeframe(timeframe)
    if timeframe == 'today':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == 'this_week':
        start = now - datetime.timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == 'this_month':
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if timeframe == 'this_year':
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def get_previous_period_boundaries(timeframe, now=None):
    now = now or timezone.now()
    current_start = timeframe_start(timeframe, now)
    if not current_start:
        return None, None, ''

    if timeframe == 'today':
        prev_start = current_start - datetime.timedelta(days=1)
        prev_end = current_start
        label = 'vs yesterday'
    elif timeframe == 'this_week':
        prev_start = current_start - datetime.timedelta(days=7)
        prev_end = current_start
        label = 'vs last week'
    elif timeframe == 'this_month':
        if current_start.month == 1:
            prev_start = current_start.replace(year=current_start.year - 1, month=12)
        else:
            prev_start = current_start.replace(month=current_start.month - 1)
        prev_end = current_start
        label = 'vs last month'
    elif timeframe == 'this_year':
        prev_start = current_start.replace(year=current_start.year - 1)
        prev_end = current_start
        label = 'vs last year'
    else:
        prev_start, prev_end, label = None, None, ''

    return prev_start, prev_end, label


def _calculate_trend(current, previous):
    current_val = float(current or 0)
    prev_val = float(previous or 0)

    if prev_val == 0:
        if current_val > 0:
            pct = 100.0
        else:
            pct = 0.0
    else:
        pct = round(((current_val - prev_val) / prev_val) * 100, 1)

    return {
        'pct': abs(pct),
        'formatted': f"{pct:+.1f}%" if pct != 0 else "0.0%",
        'is_positive': pct > 0,
        'is_negative': pct < 0,
        'is_neutral': pct == 0,
    }


def _scoped_event_slug(user, event_slug):
    event_slug = (event_slug or '').strip()
    if not event_slug:
        return ''
    exists = Event.objects.filter(owner=user, slug=event_slug).exists()
    return event_slug if exists else ''


def _organizer_version(user):
    return get_cache_version('organizer', user.pk)


def _event_version_for_slug(user, event_slug):
    if not event_slug:
        return 1
    event_id = Event.objects.filter(owner=user, slug=event_slug).values_list('id', flat=True).first()
    return get_cache_version('event', event_id) if event_id else 1


def organizer_events_queryset(user):
    return Event.objects.filter(owner=user).annotate(
        active_nominee_count=Count('nominees', filter=Q(nominees__is_active=True), distinct=True),
        total_nominee_count=Count('nominees', distinct=True),
        active_category_count=Count('competition_categories', filter=Q(competition_categories__is_active=True), distinct=True),
        total_category_count=Count('competition_categories', distinct=True),
    )


def competition_events_queryset(user):
    return organizer_events_queryset(user).filter(kind=Event.Kind.PAID_COMPETITION)


def dashboard_events_queryset(user):
    return organizer_events_queryset(user).filter(kind__in=[Event.Kind.PAID_COMPETITION, Event.Kind.TICKETED_EVENT])


def build_event_leaderboard(event):
    event_version = get_cache_version('event', event.pk)
    key = f'leaderboard:v2:event:{event.pk}:v{event_version}'

    def builder():
        return list(
            event.nominees.filter(is_active=True).select_related('category')
            .annotate(
                total_votes=Coalesce(
                    Sum('vote_purchases__quantity'),
                    Value(0),
                    output_field=IntegerField(),
                ),
                total_amount=Coalesce(
                    Sum('vote_purchases__amount_paid'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('-total_votes', '-total_amount', 'name')
        )

    return _cache_get_or_set(key, builder, LEADERBOARD_CACHE_TTL)


def _dashboard_base_querysets(user, timeframe, event_slug):
    start_date = timeframe_start(timeframe)
    purchases = VotePurchase.objects.filter(event__owner=user)
    attempts = PaymentAttempt.objects.filter(event__owner=user)
    ledger = LedgerEntry.objects.filter(
        account__owner=user,
        kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
    )
    if start_date:
        purchases = purchases.filter(paid_at__gte=start_date)
        attempts = attempts.filter(initiated_at__gte=start_date)
        ledger = ledger.filter(created_at__gte=start_date)
    if event_slug:
        purchases = purchases.filter(event__slug=event_slug)
        attempts = attempts.filter(event__slug=event_slug)
        ledger = ledger.filter(
            Q(transaction__payment_attempt__event__slug=event_slug) |
            Q(transaction__ticket_purchase__event__slug=event_slug)
        )
    return purchases, attempts, ledger


def _dashboard_ticket_querysets(user, timeframe, event_slug):
    start_date = timeframe_start(timeframe)
    purchases = TicketPurchase.objects.filter(event__owner=user, status=TicketPurchase.Status.PAID)
    attempts = TicketPurchase.objects.filter(event__owner=user)
    if start_date:
        purchases = purchases.filter(completed_at__gte=start_date)
        attempts = attempts.filter(initiated_at__gte=start_date)
    if event_slug:
        purchases = purchases.filter(event__slug=event_slug)
        attempts = attempts.filter(event__slug=event_slug)
    return purchases, attempts


def dashboard_home_context(user, *, timeframe='this_month', event_slug=''):
    timeframe = normalize_timeframe(timeframe)
    event_slug = _scoped_event_slug(user, event_slug)
    key = (
        f'dashboard-home:v6:user:{user.pk}:tf:{timeframe}:event:{event_slug or "all"}:'
        f'ov:{_organizer_version(user)}:ev:{_event_version_for_slug(user, event_slug)}'
    )

    def builder():
        events = list(dashboard_events_queryset(user).order_by('-start_at'))
        purchases_qs, attempts_qs, ledger_qs = _dashboard_base_querysets(user, timeframe, event_slug)
        ticket_purchases_qs, ticket_attempts_qs = _dashboard_ticket_querysets(user, timeframe, event_slug)

        pending_statuses = [PaymentAttempt.Status.INITIALIZED, PaymentAttempt.Status.PENDING]
        ticket_pending_statuses = [TicketPurchase.Status.INITIALIZED, TicketPurchase.Status.PENDING]

        totals = purchases_qs.aggregate(
            total_votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
            total_revenue=Coalesce(
                Sum('amount_paid'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        ticket_totals = ticket_purchases_qs.aggregate(
            total_tickets=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
            total_revenue=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )

        pending_amount = attempts_qs.filter(status__in=pending_statuses).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        pending_ticket_amount = ticket_attempts_qs.filter(status__in=ticket_pending_statuses).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']

        net_earnings = ledger_qs.aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        recent_purchases = list(
            purchases_qs.select_related('nominee', 'event').order_by('-paid_at')[:3]
        )

        top_nominees_qs = Nominee.objects.filter(event__owner=user, is_active=True).select_related('event')
        if event_slug:
            top_nominees_qs = top_nominees_qs.filter(event__slug=event_slug)
        start_date = timeframe_start(timeframe)
        vote_filter = Q()
        if start_date:
            vote_filter &= Q(vote_purchases__paid_at__gte=start_date)
        top_nominees = list(
            top_nominees_qs.annotate(
                total_votes=Coalesce(
                    Sum('vote_purchases__quantity', filter=vote_filter),
                    Value(0),
                    output_field=IntegerField(),
                )
            ).order_by('-total_votes')[:5]
        )
        nominee_milestones = []
        for nominee in top_nominees:
            votes = nominee.total_votes
            if votes < 50:
                target = 50
            elif votes < 250:
                target = 250
            elif votes < 1000:
                target = 1000
            else:
                target = ((votes // 1000) + 1) * 1000
            nominee_milestones.append(
                {
                    'nominee': nominee,
                    'votes': votes,
                    'target': target,
                    'percent': int((votes / target) * 100) if target > 0 else 0,
                }
            )

        now = timezone.now()
        stats_by_month = VotePurchase.objects.filter(event__owner=user, paid_at__year=now.year)
        if event_slug:
            stats_by_month = stats_by_month.filter(event__slug=event_slug)
        stats_by_month = (
            stats_by_month.annotate(month=ExtractMonth('paid_at'))
            .values('month')
            .annotate(votes=Sum('quantity'), revenue=Sum('amount_paid'))
            .order_by('month')
        )
        monthly_votes = [0] * 12
        monthly_revenue = [0.0] * 12
        for stat in stats_by_month:
            month = stat['month']
            if 1 <= month <= 12:
                monthly_votes[month - 1] = stat['votes'] or 0
                monthly_revenue[month - 1] = float(stat['revenue'] or 0.0)

        ticket_stats_by_month = TicketPurchase.objects.filter(
            event__owner=user, status=TicketPurchase.Status.PAID, completed_at__year=now.year
        )
        if event_slug:
            ticket_stats_by_month = ticket_stats_by_month.filter(event__slug=event_slug)
        ticket_stats_by_month = (
            ticket_stats_by_month.annotate(month=ExtractMonth('completed_at'))
            .values('month')
            .annotate(tickets=Sum('quantity'), revenue=Sum('amount'))
            .order_by('month')
        )
        monthly_tickets = [0] * 12
        for stat in ticket_stats_by_month:
            month = stat['month']
            if 1 <= month <= 12:
                monthly_tickets[month - 1] = stat['tickets'] or 0

        # Dynamic KPI Trend Calculations
        current_start = timeframe_start(timeframe, now)
        prev_start, prev_end, comparison_label = get_previous_period_boundaries(timeframe, now)
        show_comparison = (current_start is not None)

        if show_comparison:
            events_base_qs = dashboard_events_queryset(user)
            # 1. Total events created
            current_events_created = events_base_qs.filter(created_at__gte=current_start).count()
            prev_events_created = events_base_qs.filter(created_at__gte=prev_start, created_at__lt=prev_end).count()
            event_trend = _calculate_trend(current_events_created, prev_events_created)

            # 2. Published events
            current_pub_created = events_base_qs.filter(status=Event.Status.PUBLISHED, published_at__gte=current_start).count()
            prev_pub_created = events_base_qs.filter(status=Event.Status.PUBLISHED, published_at__gte=prev_start, published_at__lt=prev_end).count()
            pub_trend = _calculate_trend(current_pub_created, prev_pub_created)

            # 3. Confirmed votes (filtered by event_slug if set)
            prev_purchases_qs = VotePurchase.objects.filter(event__owner=user, paid_at__gte=prev_start, paid_at__lt=prev_end)
            if event_slug:
                prev_purchases_qs = prev_purchases_qs.filter(event__slug=event_slug)
            prev_totals = prev_purchases_qs.aggregate(
                total_votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
                total_revenue=Coalesce(
                    Sum('amount_paid'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            votes_trend = _calculate_trend(totals['total_votes'], prev_totals['total_votes'])

            # 4. Confirmed tickets (filtered by event_slug if set)
            prev_ticket_purchases_qs = TicketPurchase.objects.filter(event__owner=user, status=TicketPurchase.Status.PAID, completed_at__gte=prev_start, completed_at__lt=prev_end)
            if event_slug:
                prev_ticket_purchases_qs = prev_ticket_purchases_qs.filter(event__slug=event_slug)
            prev_ticket_totals = prev_ticket_purchases_qs.aggregate(
                total_tickets=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
                total_revenue=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            tickets_trend = _calculate_trend(ticket_totals['total_tickets'], prev_ticket_totals['total_tickets'])

            # 5. Combined revenue trend
            combined_current_revenue = totals['total_revenue'] + ticket_totals['total_revenue']
            combined_prev_revenue = prev_totals['total_revenue'] + prev_ticket_totals['total_revenue']
            revenue_trend = _calculate_trend(combined_current_revenue, combined_prev_revenue)
        else:
            event_trend = _calculate_trend(0, 0)
            pub_trend = _calculate_trend(0, 0)
            votes_trend = _calculate_trend(0, 0)
            tickets_trend = _calculate_trend(0, 0)
            revenue_trend = _calculate_trend(0, 0)

        comparison_data = {
            'show_comparison': show_comparison,
            'label': comparison_label,
            'events': event_trend,
            'published': pub_trend,
            'votes': votes_trend,
            'tickets': tickets_trend,
            'revenue': revenue_trend,
        }

        return {
            'events': events,
            'recent_purchases': recent_purchases,
            'nominee_milestones': nominee_milestones,
            'top_nominees_list': [
                {'name': nominee.name, 'votes': nominee.total_votes}
                for nominee in top_nominees
                if nominee.total_votes > 0
            ],
            'monthly_votes': monthly_votes,
            'monthly_revenue': monthly_revenue,
            'monthly_tickets': monthly_tickets,
            'timeframe': timeframe,
            'selected_event_slug': event_slug,
            'comparison': comparison_data,
            'summary': {
                'event_count': len(events),
                'published_count': sum(1 for event in events if event.status == Event.Status.PUBLISHED),
                'confirmed_votes': totals['total_votes'],
                'confirmed_tickets': ticket_totals['total_tickets'],
                'confirmed_revenue': totals['total_revenue'] + ticket_totals['total_revenue'],
                'pending_amount': pending_amount + pending_ticket_amount,
                'net_earnings': net_earnings,
                'available_to_withdraw': get_available_withdrawal_balance(user),
            },
        }

    return _cache_get_or_set(key, builder, DASHBOARD_CACHE_TTL)


def dashboard_analytics_context(user, *, timeframe='this_month', event_slug='', include_nominees=True):
    timeframe = normalize_timeframe(timeframe)
    event_slug = _scoped_event_slug(user, event_slug)
    key = (
        f'dashboard-analytics:v4:user:{user.pk}:tf:{timeframe}:event:{event_slug or "all"}:'
        f'nominees:{int(include_nominees)}:'
        f'ov:{_organizer_version(user)}:ev:{_event_version_for_slug(user, event_slug)}'
    )

    def builder():
        events = list(organizer_events_queryset(user).order_by('-start_at'))
        purchases = VotePurchase.objects.filter(event__owner=user)
        ticket_purchases = TicketPurchase.objects.filter(event__owner=user, status=TicketPurchase.Status.PAID)
        start_date = timeframe_start(timeframe)
        if start_date:
            purchases = purchases.filter(paid_at__gte=start_date)
            ticket_purchases = ticket_purchases.filter(completed_at__gte=start_date)
        if event_slug:
            purchases = purchases.filter(event__slug=event_slug)
            ticket_purchases = ticket_purchases.filter(event__slug=event_slug)
        totals = purchases.aggregate(
            votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
            revenue=Coalesce(
                Sum('amount_paid'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        ticket_totals = ticket_purchases.aggregate(
            tickets=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
            revenue=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        nominees_perf = []
        if include_nominees:
            vote_filter = Q()
            if start_date:
                vote_filter &= Q(vote_purchases__paid_at__gte=start_date)
            nominees_qs = Nominee.objects.filter(event__owner=user, is_active=True).select_related('event')
            if event_slug:
                nominees_qs = nominees_qs.filter(event__slug=event_slug)
            nominees_perf = list(
                nominees_qs.annotate(
                    votes=Coalesce(
                        Sum('vote_purchases__quantity', filter=vote_filter),
                        Value(0),
                        output_field=IntegerField(),
                    ),
                    earnings=Coalesce(
                        Sum('vote_purchases__amount_paid', filter=vote_filter),
                        Value(Decimal('0.00')),
                        output_field=DecimalField(max_digits=10, decimal_places=2),
                    ),
                ).order_by('-votes')
            )
        stats_by_day = (
            purchases.annotate(day=ExtractWeekDay('paid_at'))
            .values('day')
            .annotate(votes=Sum('quantity'))
            .order_by('day')
        )
        weekly_votes = [0] * 7
        for stat in stats_by_day:
            day_num = stat['day']
            if 1 <= day_num <= 7:
                weekly_votes[day_num - 1] = stat['votes'] or 0

        ticket_stats_by_day = (
            ticket_purchases.annotate(day=ExtractWeekDay('completed_at'))
            .values('day')
            .annotate(tickets=Sum('quantity'))
            .order_by('day')
        )
        weekly_tickets = [0] * 7
        for stat in ticket_stats_by_day:
            day_num = stat['day']
            if 1 <= day_num <= 7:
                weekly_tickets[day_num - 1] = stat['tickets'] or 0

        return {
            'events': events,
            'selected_event_slug': event_slug,
            'timeframe': timeframe,
            'nominees_perf': nominees_perf,
            'weekly_votes': weekly_votes,
            'weekly_tickets': weekly_tickets,
            'total_votes': totals['votes'],
            'total_tickets': ticket_totals['tickets'],
            'total_revenue': totals['revenue'] + ticket_totals['revenue'],
            'default_currency': user.events.values_list('currency', flat=True).first() or 'GHS',
        }

    return _cache_get_or_set(key, builder, DASHBOARD_CACHE_TTL)


def dashboard_revenue_summary_context(user):
    key = f'dashboard-revenue:v3:user:{user.pk}:ov:{_organizer_version(user)}'

    def builder():
        pending_statuses = [PaymentAttempt.Status.INITIALIZED, PaymentAttempt.Status.PENDING]
        ticket_pending_statuses = [TicketPurchase.Status.INITIALIZED, TicketPurchase.Status.PENDING]
        confirmed_votes = VotePurchase.objects.filter(event__owner=user)
        confirmed_tickets = TicketPurchase.objects.filter(event__owner=user, status=TicketPurchase.Status.PAID)
        payment_attempts = PaymentAttempt.objects.filter(event__owner=user)
        ticket_attempts = TicketPurchase.objects.filter(event__owner=user)
        ledger_entries = LedgerEntry.objects.filter(
            Q(transaction__payment_attempt__event__owner=user)
            | Q(transaction__ticket_purchase__event__owner=user)
        )
        totals = confirmed_votes.aggregate(
            gross=Coalesce(
                Sum('amount_paid'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
        )
        ticket_totals = confirmed_tickets.aggregate(
            gross=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            tickets=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
        )
        ledger_totals = ledger_entries.aggregate(
            net=Coalesce(
                Sum('amount', filter=Q(account__owner=user, kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT)),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            commission=Coalesce(
                Sum('amount', filter=Q(kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT)),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        pending_amount = payment_attempts.filter(status__in=pending_statuses).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        pending_ticket_amount = ticket_attempts.filter(status__in=ticket_pending_statuses).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        withdrawal_summary = get_withdrawal_dashboard_summary(user)
        event_rows = list(organizer_events_queryset(user).order_by('-created_at'))
        gross_by_event = {
            row['event_id']: row['total']
            for row in confirmed_votes.values('event_id').annotate(
                total=Coalesce(
                    Sum('amount_paid'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }
        for row in confirmed_tickets.values('event_id').annotate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ):
            gross_by_event[row['event_id']] = gross_by_event.get(row['event_id'], Decimal('0.00')) + row['total']
        vote_count_by_event = {
            row['event_id']: row['total']
            for row in confirmed_votes.values('event_id').annotate(
                total=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField())
            )
        }
        ticket_count_by_event = {
            row['event_id']: row['total']
            for row in confirmed_tickets.values('event_id').annotate(
                total=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField())
            )
        }
        from elections.models import ElectionVoter
        voter_count_by_event = {
            row['event_id']: row['total']
            for row in ElectionVoter.objects.filter(event__owner=user, status=ElectionVoter.Status.ELIGIBLE)
            .values('event_id')
            .annotate(total=Count('id'))
        }
        pending_by_event = {
            row['event_id']: row['total']
            for row in payment_attempts.filter(status__in=pending_statuses).values('event_id').annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }
        for row in ticket_attempts.filter(status__in=ticket_pending_statuses).values('event_id').annotate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ):
            pending_by_event[row['event_id']] = pending_by_event.get(row['event_id'], Decimal('0.00')) + row['total']
        commission_by_event = {
            row['transaction__payment_attempt__event_id']: row['total']
            for row in ledger_entries.filter(kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT)
            .exclude(transaction__payment_attempt__isnull=True)
            .values('transaction__payment_attempt__event_id')
            .annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }
        for row in ledger_entries.filter(
            kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT,
            transaction__ticket_purchase__isnull=False,
        ).values('transaction__ticket_purchase__event_id').annotate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ):
            event_id = row['transaction__ticket_purchase__event_id']
            commission_by_event[event_id] = commission_by_event.get(event_id, Decimal('0.00')) + row['total']
        earnings_by_event = {
            row['transaction__payment_attempt__event_id']: row['total']
            for row in ledger_entries.filter(
                account__owner=user,
                kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            )
            .exclude(transaction__payment_attempt__isnull=True)
            .values('transaction__payment_attempt__event_id')
            .annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }
        for row in ledger_entries.filter(
            account__owner=user,
            kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            transaction__ticket_purchase__isnull=False,
        ).values('transaction__ticket_purchase__event_id').annotate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ):
            event_id = row['transaction__ticket_purchase__event_id']
            earnings_by_event[event_id] = earnings_by_event.get(event_id, Decimal('0.00')) + row['total']
        for row in event_rows:
            row.confirmed_gross = gross_by_event.get(row.id, Decimal('0.00'))
            row.confirmed_votes = vote_count_by_event.get(row.id, 0)
            row.confirmed_tickets = ticket_count_by_event.get(row.id, 0)
            row.confirmed_voters = voter_count_by_event.get(row.id, 0)
            row.pending_amount = pending_by_event.get(row.id, Decimal('0.00'))
            row.net_earnings = earnings_by_event.get(row.id, Decimal('0.00'))
            row.platform_commission = commission_by_event.get(row.id, Decimal('0.00'))
        return {
            'summary': {
                'confirmed_gross_revenue': totals['gross'] + ticket_totals['gross'],
                'confirmed_vote_revenue': totals['gross'],
                'confirmed_ticket_revenue': ticket_totals['gross'],
                'confirmed_vote_total': totals['votes'],
                'confirmed_ticket_total': ticket_totals['tickets'],
                'pending_amount': pending_amount + pending_ticket_amount,
                'net_earnings': ledger_totals['net'],
                'commission_total': ledger_totals['commission'],
                'available_to_withdraw': withdrawal_summary['available_to_withdraw'],
                'total_withdrawn': withdrawal_summary['total_withdrawn'],
            },
            'event_rows': event_rows,
            'default_currency': user.events.values_list('currency', flat=True).first() or 'GHS',
        }

    return _cache_get_or_set(key, builder, DASHBOARD_CACHE_TTL)


def dashboard_revenue_lists_context(user, request_get):
    from django.core.paginator import Paginator

    payment_attempts = PaymentAttempt.objects.filter(event__owner=user).select_related(
        'event', 'nominee'
    ).order_by('-initiated_at')
    ticket_purchases = TicketPurchase.objects.filter(event__owner=user).select_related(
        'event', 'ticket_type'
    ).order_by('-initiated_at')
    ledger_transactions = LedgerTransaction.objects.filter(
        Q(payment_attempt__event__owner=user) | Q(ticket_purchase__event__owner=user)
    ).select_related(
        'payment_attempt',
        'payment_attempt__event',
        'payment_attempt__nominee',
        'ticket_purchase',
        'ticket_purchase__event',
        'ticket_purchase__ticket_type',
    ).order_by('-posted_at')
    paginator_successful = Paginator(payment_attempts.filter(status=PaymentAttempt.Status.PAID), 10)
    paginator_attention = Paginator(
        payment_attempts.filter(
            status__in=[
                PaymentAttempt.Status.INITIALIZED,
                PaymentAttempt.Status.PENDING,
                PaymentAttempt.Status.FAILED,
                PaymentAttempt.Status.CANCELLED,
            ]
        ),
        10,
    )
    paginator_ledger = Paginator(ledger_transactions, 10)
    return {
        'recent_successful_payments': paginator_successful.get_page(request_get.get('page_payments', 1)),
        'recent_attention_payments': paginator_attention.get_page(request_get.get('page_attention', 1)),
        'recent_ledger_transactions': paginator_ledger.get_page(request_get.get('page_ledger', 1)),
        'recent_successful_ticket_purchases': Paginator(
            ticket_purchases.filter(status=TicketPurchase.Status.PAID),
            10,
        ).get_page(request_get.get('page_tickets', 1)),
    }


def withdrawal_summary_fast(user):
    confirmed = LedgerEntry.objects.filter(
        account__owner=user,
        kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
    ).aggregate(
        total=Coalesce(
            Sum('amount'),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    )['total']
    withdrawals = WithdrawalRequest.objects.filter(organizer=user).aggregate(
        completed=Coalesce(
            Sum('amount', filter=Q(status=WithdrawalRequest.Status.COMPLETED)),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        in_progress=Coalesce(
            Sum('amount', filter=Q(status__in=WITHDRAWAL_IN_PROGRESS_STATUSES)),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        pending=Coalesce(
            Sum('amount', filter=Q(status=WithdrawalRequest.Status.PENDING)),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        reserved=Coalesce(
            Sum(
                'amount',
                filter=Q(
                    status__in=[
                        WithdrawalRequest.Status.APPROVED,
                        WithdrawalRequest.Status.PROCESSING,
                        WithdrawalRequest.Status.COMPLETED,
                    ]
                ),
            ),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
    )
    available = max(confirmed - withdrawals['reserved'], Decimal('0.00')).quantize(Decimal('0.01'))
    return {
        'confirmed_earnings': confirmed,
        'available_to_withdraw': available,
        'total_withdrawn': withdrawals['completed'],
        'in_progress_total': withdrawals['in_progress'],
        'pending_review_total': withdrawals['pending'],
    }


def build_tally_fast(event):
    positions = list(
        event.election_positions.filter(is_active=True)
        .prefetch_related('candidates')
        .order_by('display_order', 'title')
    )
    selection_rows = (
        BallotSelection.objects.filter(position__event=event)
        .values('position_id', 'candidate_id')
        .annotate(votes=Count('id'))
    )
    counts = {
        (row['position_id'], row['candidate_id']): row['votes']
        for row in selection_rows
    }
    totals = []
    for position in positions:
        candidates = [
            {
                'candidate_id': candidate.id,
                'candidate_name': candidate.name,
                'votes': counts.get((position.id, candidate.id), 0),
            }
            for candidate in position.candidates.all()
            if candidate.is_active
        ]
        totals.append(
            {
                'position_id': position.id,
                'position_title': position.title,
                'candidates': candidates,
                'abstentions': counts.get((position.id, None), 0),
            }
        )
    return totals
