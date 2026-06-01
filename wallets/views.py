from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import TemplateView
from django.http import HttpResponse

from payments.services import get_paystack_banks, resolve_paystack_account
from payments.models import PaymentAttempt

from .forms import WithdrawalRequestForm
from .models import WithdrawalRequest
from .services import (
    get_organizer_account,
)
from events.performance import (
    dashboard_analytics_context,
    dashboard_revenue_lists_context,
    dashboard_revenue_summary_context,
    withdrawal_summary_fast,
)


class DashboardRevenueView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/revenue.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(dashboard_revenue_summary_context(self.request.user))
        context.update(dashboard_revenue_lists_context(self.request.user, self.request.GET))

        # Dynamic currency and commission rate for templates
        from django.conf import settings as app_settings
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
            # Delete OTP from cache immediately to prevent replay/reuse
            from django.core.cache import cache
            cache.delete(f'withdrawal_otp_{request.user.pk}')

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
        summary = withdrawal_summary_fast(self.request.user)
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
            import json
            import html
            data = resolve_paystack_account(account_number, bank_code)
            account_name = data.get('account_name', '')
            safe_name = json.dumps(account_name)
            escaped_name = html.escape(account_name)
            return HttpResponse(f"""
                <script>
                    var nameInput = document.getElementById('id_payout_name');
                    if(nameInput) {{
                        nameInput.value = {safe_name};
                        nameInput.classList.remove('opacity-80');
                    }}
                </script>
                <p class="text-xs text-vc-success font-medium">✓ Verified: {escaped_name}</p>
            """)
        except Exception as e:
            return HttpResponse(f'<p class="text-xs text-vc-danger font-medium">⚠ {html.escape(str(e))}</p>')


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
        context.update(
            dashboard_analytics_context(
                self.request.user,
                timeframe=self.request.GET.get('timeframe', 'this_month'),
                event_slug=self.request.GET.get('event', ''),
                include_nominees=False,
            )
        )
        return context


class DashboardAnalyticsNomineesFragmentView(LoginRequiredMixin, View):
    template_name = 'dashboard/fragments/_analytics_nominees.html'

    def get(self, request, *args, **kwargs):
        context = dashboard_analytics_context(
            request.user,
            timeframe=request.GET.get('timeframe', 'this_month'),
            event_slug=request.GET.get('event', ''),
            include_nominees=True,
        )
        return render(request, self.template_name, context)
