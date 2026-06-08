import json
from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt

from events.models import Event
from nominees.models import Nominee
from votecentral.rate_limits import is_rate_limited

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
class PaystackInitiateView(View):
    def post(self, request, *args, **kwargs):
        if is_rate_limited(request, 'paystack-init', 10, 60):
            return HttpResponse('Too many requests.', status=429)

        form = PaymentInitiationForm(request.POST)
        if not form.is_valid():
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': 'Please provide valid payment details.'
                }, status=400)
            messages.error(request, 'Please provide valid payment details.')
            return redirect('events:home')

        event = get_object_or_404(Event.objects.published(), slug=form.cleaned_data['event_slug'])
        try:
            nominee = Nominee.resolve_for_event(event, form.cleaned_data['nominee_ref'])
        except Nominee.DoesNotExist:
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': 'Nominee not found.'
                }, status=400)
            messages.error(request, 'Nominee not found.')
            return redirect(event.get_absolute_url())

        if not event.has_platform_commission():
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': 'This event is not ready to accept votes yet.'
                }, status=400)
            messages.error(request, 'This event is not ready to accept votes yet.')
            return redirect(event.get_absolute_url())

        if not nominee.is_active or not event.accepts_votes():
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': 'Voting is not available for this event right now.'
                }, status=400)
            messages.error(request, 'Voting is not available for this event right now.')
            return redirect(event.get_absolute_url())

        quantity = form.cleaned_data['quantity']
        
        bundle = event.vote_bundles.filter(quantity=quantity, is_active=True).first()
        if bundle:
            amount = bundle.price
        else:
            amount = (event.vote_price or Decimal('0.00')) * quantity
        payment_attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency=event.currency,
            platform_commission_percent=event.platform_commission_percent,
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
        except (OSError, RuntimeError, ValueError) as exc:
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
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': 'Unable to initialize payment right now.'
                }, status=400)
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

        if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
            return JsonResponse({
                'status': 'success',
                'access_code': payment_attempt.gateway_access_code,
                'reference': payment_attempt.gateway_reference,
                'checkout_url': payment_attempt.gateway_checkout_url,
                'callback_url': reverse('payments:paystack_callback') + f'?reference={payment_attempt.gateway_reference}&status=success',
                'amount': float(payment_attempt.amount),
                'currency': payment_attempt.currency,
                'email': payment_attempt.voter_email,
                'voter_name': payment_attempt.voter_name,
                'nominee_name': nominee.name,
                'quantity': quantity,
            })

        return redirect(payment_attempt.gateway_checkout_url)


@method_decorator(csrf_exempt, name='dispatch')
class PaystackWebhookView(View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        if is_rate_limited(request, 'paystack-webhook', 120, 60):
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
                try:
                    from ticketing.models import TicketPurchase
                    from ticketing.services import handle_ticket_paystack_webhook

                    handle_ticket_paystack_webhook(payload)
                except TicketPurchase.DoesNotExist:
                    return HttpResponse('Ignored.', status=200)

        return HttpResponse('OK', status=200)


class PaystackCallbackView(View):
    def get(self, request, *args, **kwargs):
        reference = request.GET.get('reference') or request.GET.get('trxref')
        if not reference:
            messages.warning(request, 'We could not find a payment reference in the callback response.')
            return redirect('payments:status_lookup')

        # Directly verify and settle transaction from server callback
        from .services import verify_and_process_paystack_payment
        verify_and_process_paystack_payment(reference)

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
                try:
                    from ticketing.models import TicketPurchase
                    from ticketing.services import (
                        record_ticket_paystack_callback,
                        ticket_purchase_status_redirect_url,
                    )

                    ticket_purchase = TicketPurchase.objects.select_related('event', 'ticket_type').get(
                        gateway_reference=reference
                    )
                except TicketPurchase.DoesNotExist:
                    return redirect('payments:status_detail', reference=reference)

                callback_status = request.GET.get('status', '').strip().lower()
                record_ticket_paystack_callback(ticket_purchase, callback_status=callback_status)

                if ticket_purchase.status == TicketPurchase.Status.PAID:
                    messages.success(request, f"Ticket payment successful! Your tickets for '{ticket_purchase.event.title}' are ready.")
                elif ticket_purchase.status in {TicketPurchase.Status.FAILED, TicketPurchase.Status.CANCELLED}:
                    messages.error(request, f"Ticket payment for '{ticket_purchase.event.title}' was unsuccessful or cancelled.")
                else:
                    messages.info(request, f"Ticket payment for '{ticket_purchase.event.title}' is pending confirmation.")
                return redirect(ticket_purchase_status_redirect_url(ticket_purchase))
            callback_status = request.GET.get('status', '').strip().lower()
            record_organizer_paystack_callback(organizer_attempt, callback_status=callback_status)
            
            if organizer_attempt.status == OrganizerPaymentAttempt.Status.PAID:
                messages.success(request, f"Payment successful! The fee for election '{organizer_attempt.event.title}' has been securely paid.")
            elif organizer_attempt.status in {OrganizerPaymentAttempt.Status.FAILED, OrganizerPaymentAttempt.Status.CANCELLED}:
                messages.error(request, f"Payment for election '{organizer_attempt.event.title}' was unsuccessful or cancelled.")
            else:
                messages.info(request, f"Payment for election '{organizer_attempt.event.title}' is pending confirmation.")
            return redirect(organizer_payment_status_redirect_url(organizer_attempt))

        callback_status = request.GET.get('status', '').strip().lower()
        record_paystack_callback(attempt, callback_status=callback_status)
        
        if attempt.status == PaymentAttempt.Status.PAID:
            messages.success(request, f"Payment successful! {attempt.vote_quantity} votes have been securely recorded for {attempt.nominee.name}.")
        elif attempt.status in {PaymentAttempt.Status.FAILED, PaymentAttempt.Status.CANCELLED}:
            messages.error(request, f"Payment was unsuccessful or cancelled. No votes were recorded.")
        else:
            messages.info(request, f"Payment is pending confirmation. Your votes will count once Paystack confirms it.")
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
