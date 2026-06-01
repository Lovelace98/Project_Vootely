import json
from decimal import Decimal

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt

from events.models import Event
from nominees.models import Nominee

from .forms import PaymentInitiationForm
from .models import PaymentAttempt
from .services import (
    generate_reference,
    handle_paystack_webhook,
    initialize_paystack_transaction,
    payment_status_redirect_url,
    record_paystack_callback,
    resolve_payment_status_by_reference,
    verify_paystack_signature,
)


def rate_limit(request, key_prefix, limit, window_seconds):
    ip_address = request.META.get('REMOTE_ADDR', 'unknown')
    cache_key = f'{key_prefix}:{ip_address}'
    added = cache.add(cache_key, 1, timeout=window_seconds)
    if added:
        return False
    count = cache.incr(cache_key)
    return count > limit


class PaystackInitiateView(View):
    def post(self, request, *args, **kwargs):
        if rate_limit(request, 'paystack-init', 10, 60):
            return HttpResponse('Too many requests.', status=429)

        form = PaymentInitiationForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Please provide valid payment details.')
            return redirect('events:home')

        event = get_object_or_404(Event.objects.published(), slug=form.cleaned_data['event_slug'])
        try:
            nominee = Nominee.resolve_for_event(event, form.cleaned_data['nominee_ref'])
        except Nominee.DoesNotExist:
            messages.error(request, 'Nominee not found.')
            return redirect(event.get_absolute_url())

        if not nominee.is_active or not event.accepts_votes():
            messages.error(request, 'Voting is not available for this event right now.')
            return redirect(event.get_absolute_url())

        quantity = form.cleaned_data['quantity']
        amount = (event.vote_price or Decimal('0.00')) * quantity
        payment_attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency=event.currency,
            vote_quantity=quantity,
            voter_name=form.cleaned_data['voter_name'],
            voter_email=form.cleaned_data['voter_email'],
            voter_phone=form.cleaned_data['voter_phone'],
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            gateway_reference=generate_reference(),
            status=PaymentAttempt.Status.INITIALIZED,
            metadata={'created_at': timezone.now().isoformat()},
        )

        try:
            payload = initialize_paystack_transaction(payment_attempt)
        except Exception as exc:
            payment_attempt.status = PaymentAttempt.Status.FAILED
            payment_attempt.gateway_status = 'initialize_failed'
            payment_attempt.failure_reason = str(exc)[:255]
            payment_attempt.completed_at = timezone.now()
            payment_attempt.gateway_response = {'error': str(exc)}
            payment_attempt.save(
                update_fields=[
                    'status',
                    'gateway_status',
                    'failure_reason',
                    'completed_at',
                    'gateway_response',
                ]
            )
            messages.error(request, 'Unable to initialize payment right now.')
            return redirect(nominee.get_absolute_url())

        data = payload.get('data') or {}
        payment_attempt.status = PaymentAttempt.Status.PENDING
        payment_attempt.gateway_access_code = data.get('access_code', '')
        payment_attempt.gateway_checkout_url = data.get('authorization_url', '')
        payment_attempt.gateway_response = payload
        payment_attempt.save(
            update_fields=[
                'status',
                'gateway_access_code',
                'gateway_checkout_url',
                'gateway_response',
            ]
        )
        return redirect(payment_attempt.gateway_checkout_url)


@method_decorator(csrf_exempt, name='dispatch')
class PaystackWebhookView(View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        if rate_limit(request, 'paystack-webhook', 120, 60):
            return HttpResponse('Too many requests.', status=429)

        signature = request.headers.get('x-paystack-signature', '')
        if not verify_paystack_signature(request.body, signature):
            return HttpResponseForbidden('Invalid signature.')

        try:
            payload = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            return HttpResponseBadRequest('Invalid payload.')

        try:
            handle_paystack_webhook(payload)
        except PaymentAttempt.DoesNotExist:
            try:
                from elections.models import OrganizerPaymentAttempt
                from elections.services import handle_organizer_paystack_webhook

                handle_organizer_paystack_webhook(payload)
            except OrganizerPaymentAttempt.DoesNotExist:
                return HttpResponse('Ignored.', status=200)

        return HttpResponse('OK', status=200)


class PaystackCallbackView(View):
    def get(self, request, *args, **kwargs):
        reference = request.GET.get('reference') or request.GET.get('trxref')
        if not reference:
            messages.warning(request, 'We could not find a payment reference in the callback response.')
            return redirect('payments:status_lookup')

        try:
            attempt = PaymentAttempt.objects.select_related('event', 'nominee').get(
                gateway_reference=reference
            )
        except PaymentAttempt.DoesNotExist:
            try:
                from elections.models import OrganizerPaymentAttempt
                from elections.services import (
                    organizer_payment_status_redirect_url,
                    record_organizer_paystack_callback,
                )

                organizer_attempt = OrganizerPaymentAttempt.objects.select_related('event', 'invoice').get(
                    gateway_reference=reference
                )
            except OrganizerPaymentAttempt.DoesNotExist:
                return redirect('payments:status_detail', reference=reference)
            callback_status = request.GET.get('status', '')
            record_organizer_paystack_callback(organizer_attempt, callback_status=callback_status)
            return redirect(organizer_payment_status_redirect_url(organizer_attempt))

        callback_status = request.GET.get('status', '')
        record_paystack_callback(attempt, callback_status=callback_status)
        return redirect(payment_status_redirect_url(attempt))


class PaymentStatusView(TemplateView):
    template_name = 'payments/status_detail.html'

    def get_reference(self):
        return (self.kwargs.get('reference') or self.request.GET.get('reference') or '').strip()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reference = self.get_reference()
        payment_status = resolve_payment_status_by_reference(reference)
        show_payment_details = (
            payment_status.get('show_payment_details', False) if payment_status else False
        )
        payment_attempt = (
            payment_status.get('payment_attempt')
            if payment_status and show_payment_details
            else None
        )
        payment_status_poll_url = ''
        return_url = reverse('events:home')

        if reference:
            payment_status_poll_url = reverse('payments:status_panel', args=[reference])
        if payment_attempt:
            return_url = payment_attempt.nominee.get_absolute_url()

        context.update(
            {
                'payment_status': payment_status,
                'payment_attempt': payment_attempt,
                'payment_reference': reference,
                'payment_status_poll_url': payment_status_poll_url,
                'return_url': return_url,
            }
        )
        return context


class PaymentStatusPanelView(TemplateView):
    template_name = 'payments/_payment_status.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reference = self.kwargs['reference']
        context['payment_status'] = resolve_payment_status_by_reference(reference)
        context['payment_reference'] = reference
        context['payment_status_poll_url'] = reverse('payments:status_panel', args=[reference])
        return context
