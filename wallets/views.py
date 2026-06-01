from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import DecimalField, IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView
from django.http import HttpResponse

from payments.services import get_paystack_banks, resolve_paystack_account
from events.models import Event
from payments.models import PaymentAttempt
from votes.models import VotePurchase

from .forms import WithdrawalRequestForm
from .models import LedgerEntry, LedgerTransaction, WithdrawalRequest
from .services import (
    get_organizer_account,
    get_withdrawal_dashboard_summary,
)


class DashboardRevenueView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/revenue.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending_statuses = [
            PaymentAttempt.Status.INITIALIZED,
            PaymentAttempt.Status.PENDING,
        ]

        confirmed_votes = VotePurchase.objects.filter(event__owner=self.request.user)
        payment_attempts = PaymentAttempt.objects.filter(
            event__owner=self.request.user
        ).select_related('event', 'nominee').order_by('-initiated_at')
        ledger_entries = LedgerEntry.objects.filter(
            transaction__payment_attempt__event__owner=self.request.user
        ).select_related('transaction', 'transaction__payment_attempt', 'account')
        ledger_transactions = LedgerTransaction.objects.filter(
            payment_attempt__event__owner=self.request.user
        ).select_related(
            'payment_attempt',
            'payment_attempt__event',
            'payment_attempt__nominee',
        ).order_by('-posted_at')

        confirmed_gross_revenue = confirmed_votes.aggregate(
            total=Coalesce(
                Sum('amount_paid'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        confirmed_vote_total = confirmed_votes.aggregate(
            total=Coalesce(
                Sum('quantity'),
                Value(0),
                output_field=IntegerField(),
            )
        )['total']
        pending_amount = payment_attempts.filter(status__in=pending_statuses).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        net_earnings = ledger_entries.filter(
            account__owner=self.request.user,
            kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
        ).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        commission_total = ledger_entries.filter(
            kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT
        ).aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        withdrawal_summary = get_withdrawal_dashboard_summary(self.request.user)

        event_rows = list(Event.objects.filter(owner=self.request.user))
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
        vote_count_by_event = {
            row['event_id']: row['total']
            for row in confirmed_votes.values('event_id').annotate(
                total=Coalesce(
                    Sum('quantity'),
                    Value(0),
                    output_field=IntegerField(),
                )
            )
        }
        pending_by_event = {
            row['event_id']: row['total']
            for row in payment_attempts.filter(status__in=pending_statuses)
            .values('event_id')
            .annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }

        commission_by_event = {
            row['transaction__payment_attempt__event_id']: row['total']
            for row in ledger_entries.filter(
                kind=LedgerEntry.Kind.PLATFORM_FEE_CREDIT
            )
            .values('transaction__payment_attempt__event_id')
            .annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }
        earnings_by_event = {
            row['transaction__payment_attempt__event_id']: row['total']
            for row in ledger_entries.filter(
                account__owner=self.request.user,
                kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            )
            .values('transaction__payment_attempt__event_id')
            .annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        }

        for row in event_rows:
            row.confirmed_gross = gross_by_event.get(row.id, Decimal('0.00'))
            row.confirmed_votes = vote_count_by_event.get(row.id, 0)
            row.pending_amount = pending_by_event.get(row.id, Decimal('0.00'))
            row.net_earnings = earnings_by_event.get(row.id, Decimal('0.00'))
            row.platform_commission = commission_by_event.get(row.id, Decimal('0.00'))

        context['summary'] = {
            'confirmed_gross_revenue': confirmed_gross_revenue,
            'confirmed_vote_total': confirmed_vote_total,
            'pending_amount': pending_amount,
            'net_earnings': net_earnings,
            'commission_total': commission_total,
            'available_to_withdraw': withdrawal_summary['available_to_withdraw'],
            'total_withdrawn': withdrawal_summary['total_withdrawn'],
        }
        from django.core.paginator import Paginator
        
        paginator_successful = Paginator(payment_attempts.filter(status=PaymentAttempt.Status.PAID), 10)
        page_successful = self.request.GET.get('page_payments', 1)
        context['recent_successful_payments'] = paginator_successful.get_page(page_successful)
        
        paginator_attention = Paginator(payment_attempts.filter(status__in=[
            PaymentAttempt.Status.INITIALIZED,
            PaymentAttempt.Status.PENDING,
            PaymentAttempt.Status.FAILED,
            PaymentAttempt.Status.CANCELLED,
        ]), 10)
        page_attention = self.request.GET.get('page_attention', 1)
        context['recent_attention_payments'] = paginator_attention.get_page(page_attention)
        
        paginator_ledger = Paginator(ledger_transactions, 10)
        page_ledger = self.request.GET.get('page_ledger', 1)
        context['recent_ledger_transactions'] = paginator_ledger.get_page(page_ledger)
        context['event_rows'] = event_rows

        # Dynamic currency and commission rate for templates
        default_currency = self.request.user.events.values_list('currency', flat=True).first() or 'GHS'
        from django.conf import settings as app_settings
        context['default_currency'] = default_currency
        context['commission_rate'] = getattr(app_settings, 'PLATFORM_COMMISSION_PERCENT', 10)
        return context


class DashboardWithdrawalsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/withdrawals.html'

    def get_default_currency(self):
        return self.request.user.events.values_list('currency', flat=True).first() or 'GHS'

    def get_form(self):
        return WithdrawalRequestForm(organizer=self.request.user)

    def post(self, request, *args, **kwargs):
        form = WithdrawalRequestForm(request.POST, organizer=request.user)
        if form.is_valid():
            withdrawal = form.save(commit=False)
            withdrawal.organizer = request.user
            withdrawal.wallet_account = get_organizer_account(request.user)
            withdrawal.currency = self.get_default_currency()
            
            # Resolve bank name from code
            from payments.services import get_paystack_banks
            banks = get_paystack_banks(type='ghipss' if withdrawal.payout_type == 'bank' else 'mobile_money')
            bank_map = {b['code']: b['name'] for b in banks}
            withdrawal.bank_name = bank_map.get(withdrawal.bank_code, 'Unknown Provider')
            
            withdrawal.save()
            from notifications.services import queue_withdrawal_requested_notifications

            queue_withdrawal_requested_notifications(withdrawal)
            messages.success(
                request,
                'Withdrawal request submitted. VoteCentral staff will review it before payout.',
            )
            return redirect('dashboard:withdrawals')
        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        summary = get_withdrawal_dashboard_summary(self.request.user)
        context['form'] = kwargs.get('form') or self.get_form()
        context['summary'] = summary
        context['withdrawals'] = WithdrawalRequest.objects.filter(
            organizer=self.request.user
        ).select_related('reviewed_by')
        return context


class ResolveAccountView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        account_number = request.GET.get('bank_account_number')
        bank_code = request.GET.get('bank_code')
        if not account_number or not bank_code:
            return HttpResponse('<p class="text-xs text-vc-dark-300 italic">Enter both provider and account number to verify...</p>')

        try:
            from django.core.exceptions import ValidationError
            data = resolve_paystack_account(account_number, bank_code)
            account_name = data.get('account_name', '').replace('"', '\\"')
            return HttpResponse(f"""
                <script>
                    var nameInput = document.getElementById('id_payout_name');
                    if(nameInput) {{
                        nameInput.value = "{account_name}";
                        nameInput.classList.remove('opacity-80');
                    }}
                </script>
                <p class="text-xs text-vc-success font-medium">✓ Verified: {account_name}</p>
            """)
        except Exception as e:
            return HttpResponse(f'<p class="text-xs text-vc-danger font-medium">⚠ {str(e)}</p>')


class BankListPartialView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        payout_type = request.GET.get('payout_type', 'mobile_money')
        from payments.services import get_paystack_banks
        banks = get_paystack_banks(type='ghipss' if payout_type == 'bank' else 'mobile_money')
        options = "".join([f'<option value="{b["code"]}">{b["name"]}</option>' for b in banks])
        return HttpResponse(f'<option value="">--- Select Provider ---</option>{options}')


class RequestOTPView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        import random
        from django.core.cache import cache
        from django.core.mail import send_mail
        
        otp = str(random.randint(100000, 999999))
        cache.set(f'withdrawal_otp_{request.user.pk}', otp, 600)  # 10 minutes
        
        try:
            send_mail(
                'Security Code: VoteCentral Withdrawal',
                f'Your withdrawal security code is: {otp}. It will expire in 10 minutes. If you did not request this, please change your password immediately.',
                'security@votecentral.com',
                [request.user.email],
                fail_silently=False,
            )
            return HttpResponse('<p class="text-xs text-vc-success font-medium">✓ Code sent to your email</p>')
        except Exception as e:
            # Fallback for dev/no email configured
            return HttpResponse(f'<p class="text-xs text-vc-warning font-medium">⚠ Sent (Dev): {otp}</p>')


from django.views.generic import TemplateView
import datetime

class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/analytics.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.utils import timezone
        from django.db.models.functions import ExtractMonth, ExtractWeekDay
        from django.db.models import Q
        from nominees.models import Nominee

        # Filters
        event_slug = self.request.GET.get('event', '').strip()
        timeframe = self.request.GET.get('timeframe', 'this_month').strip().lower()
        
        now = timezone.now()
        start_date = None
        if timeframe == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif timeframe == 'this_week':
            start_date = now - datetime.timedelta(days=now.weekday())
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        elif timeframe == 'this_month':
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif timeframe == 'this_year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

        # Base queries
        events = Event.objects.filter(owner=self.request.user)
        purchases = VotePurchase.objects.filter(event__owner=self.request.user)
        
        if start_date:
            purchases = purchases.filter(paid_at__gte=start_date)
        if event_slug:
            purchases = purchases.filter(event__slug=event_slug)
            
        totals = purchases.aggregate(
            votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
            revenue=Coalesce(Sum('amount_paid'), Value(Decimal('0.00')), output_field=DecimalField(max_digits=10, decimal_places=2))
        )
        
        # Group sales by nominee
        nominees_qs = Nominee.objects.filter(event__owner=self.request.user, is_active=True)
        if event_slug:
            nominees_qs = nominees_qs.filter(event__slug=event_slug)
            
        nominees_perf = nominees_qs.annotate(
            votes=Coalesce(Sum('vote_purchases__quantity'), Value(0), output_field=IntegerField()),
            earnings=Coalesce(Sum('vote_purchases__amount_paid'), Value(Decimal('0.00')), output_field=DecimalField(max_digits=10, decimal_places=2))
        ).order_by('-votes')
        
        # Dynamic weekly aggregates (votes cast per day of the week)
        stats_by_day = purchases.annotate(
            day=ExtractWeekDay('paid_at')
        ).values('day').annotate(
            votes=Sum('quantity')
        ).order_by('day')
        
        weekly_votes = [0] * 7
        for stat in stats_by_day:
            day_num = stat['day']
            if 1 <= day_num <= 7:
                weekly_votes[day_num-1] = stat['votes'] or 0

        default_currency = self.request.user.events.values_list('currency', flat=True).first() or 'GHS'

        context['events'] = events
        context['selected_event_slug'] = event_slug
        context['timeframe'] = timeframe
        context['nominees_perf'] = nominees_perf
        context['weekly_votes'] = weekly_votes
        context['total_votes'] = totals['votes']
        context['total_revenue'] = totals['revenue']
        context['default_currency'] = default_currency
        return context
