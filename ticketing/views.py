from decimal import Decimal
import json
import re

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from events.forms import TicketedEventForm
from events.models import Event
from votecentral.mixins import SafeIntegrityMixin
from votecentral.rate_limits import is_rate_limited

from .forms import (
    TicketPurchaseForm,
    TicketScannerActivationForm,
    TicketScannerCredentialResetForm,
    TicketScannerPassForm,
    TicketTypeForm,
)
from .models import Ticket, TicketProvisionalEntry, TicketPurchase, TicketScannerPass, TicketType, scanner_pass_default_expiry
from .services import (
    build_ticket_purchase_status,
    check_in_ticket,
    create_ticket_purchase,
    initialize_paystack_ticket_transaction,
    sync_provisional_entry,
)


def normalize_event_code(value):
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper())


def scanner_session_key(scanner_pass):
    return f'ticket_scanner_pass_{scanner_pass.pk}'


def ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


SCANNER_SHARE_SESSION_KEY = 'ticket_scanner_share_payload'


def scanner_pass_share_payload(request, scanner_pass, pin):
    share_url = request.build_absolute_uri(scanner_pass.get_absolute_url())
    staff_label = scanner_pass.staff_label or 'Gate staff'
    expires = timezone.localtime(scanner_pass.expires_at).strftime('%b %d, %Y %I:%M %p').replace(' 0', ' ')
    message = (
        f'Vootely scanner access for {scanner_pass.event.title}\n'
        f'Gate: {scanner_pass.gate_name}\n'
        f'Staff: {staff_label}\n'
        f'Link: {share_url}\n'
        f'PIN: {pin}\n'
        f'Expires: {expires}\n'
        'Open the link on the gate phone and enter the PIN once.'
    )
    return {
        'event_id': scanner_pass.event_id,
        'pass_id': scanner_pass.pk,
        'gate_name': scanner_pass.gate_name,
        'staff_label': staff_label,
        'share_url': share_url,
        'pin': pin,
        'expires_at': scanner_pass.expires_at.isoformat(),
        'expires_label': expires,
        'message': message,
    }


def stash_scanner_share_payload(request, scanner_pass, pin):
    request.session[SCANNER_SHARE_SESSION_KEY] = scanner_pass_share_payload(request, scanner_pass, pin)
    request.session.modified = True


def ticket_doorlist_for_event(event):
    tickets = (
        Ticket.objects.filter(event=event, purchase__status=TicketPurchase.Status.PAID)
        .select_related('ticket_type', 'purchase', 'checked_in_by')
        .order_by('purchase__buyer_name', 'purchase__buyer_email', 'code')
    )
    doorlist = []
    for ticket in tickets:
        purchase = ticket.purchase
        searchable = ' '.join(
            [
                ticket.code,
                purchase.gateway_reference,
                purchase.buyer_name,
                purchase.buyer_email,
                purchase.buyer_phone,
                ticket.ticket_type.name,
            ]
        ).lower()
        doorlist.append(
            {
                'code': ticket.code,
                'status': ticket.status,
                'buyer_name': purchase.buyer_name,
                'buyer_email': purchase.buyer_email,
                'buyer_phone': purchase.buyer_phone,
                'ticket_type': ticket.ticket_type.name,
                'purchase_reference': purchase.gateway_reference,
                'used_at': ticket.used_at.isoformat() if ticket.used_at else '',
                'checked_in_by': ticket.checked_in_by.email if ticket.checked_in_by_id else '',
                'searchable': searchable,
            }
        )
    return doorlist


class OrganizerTicketEventMixin(LoginRequiredMixin):
    allowed_kinds = (
        Event.Kind.PAID_COMPETITION,
        Event.Kind.SECURE_ELECTION,
        Event.Kind.TICKETED_EVENT,
    )

    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('owner'),
                slug=self.kwargs.get('slug') or self.kwargs.get('event_slug'),
                owner=self.request.user,
                kind__in=self.allowed_kinds,
            )
        return self._event


class TicketPurchaseInitiateView(View):
    def post(self, request, *args, **kwargs):
        if is_rate_limited(request, 'ticket-paystack-init', 10, 60):
            return JsonResponse({'status': 'error', 'message': 'Too many requests.'}, status=429)

        form = TicketPurchaseForm(request.POST)
        wants_json = (
            (request.headers.get('Accept') and 'application/json' in request.headers.get('Accept', ''))
            or request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or request.POST.get('inline') == 'true'
        )
        if not form.is_valid():
            if wants_json:
                return JsonResponse({'status': 'error', 'message': 'Please provide valid ticket details.'}, status=400)
            messages.error(request, 'Please provide valid ticket details.')
            return redirect('events:home')

        event = get_object_or_404(
            Event.objects.published(),
            slug=form.cleaned_data['event_slug'],
        )

        with transaction.atomic():
            ticket_type = get_object_or_404(
                TicketType.objects.select_for_update().select_related('event'),
                pk=form.cleaned_data['ticket_type_id'],
                event=event,
            )
            try:
                purchase = create_ticket_purchase(
                    ticket_type=ticket_type,
                    quantity=form.cleaned_data['quantity'],
                    buyer_name=form.cleaned_data['buyer_name'],
                    buyer_email=form.cleaned_data['buyer_email'],
                    buyer_phone=form.cleaned_data['buyer_phone'],
                    ip_address=request.META.get('REMOTE_ADDR') or None,
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    metadata={'created_at': timezone.now().isoformat(), 'source': 'web'},
                )
            except ValidationError as exc:
                message = exc.messages[0] if hasattr(exc, 'messages') else str(exc)
                if wants_json:
                    return JsonResponse({'status': 'error', 'message': message}, status=400)
                messages.error(request, message)
                return redirect(event.get_absolute_url())

        try:
            payload = initialize_paystack_ticket_transaction(purchase)
        except (OSError, RuntimeError, ValueError) as exc:
            purchase.status = TicketPurchase.Status.FAILED
            purchase.gateway_status = 'initialize_failed'
            purchase.failure_reason = str(exc)[:255]
            purchase.completed_at = timezone.now()
            purchase.gateway_response = {'error': str(exc)}
            purchase.save(
                update_fields=[
                    'status',
                    'gateway_status',
                    'failure_reason',
                    'completed_at',
                    'gateway_response',
                ]
            )
            if wants_json:
                return JsonResponse({'status': 'error', 'message': 'Unable to initialize ticket payment right now.'}, status=400)
            messages.error(request, 'Unable to initialize ticket payment right now.')
            return redirect(event.get_absolute_url())

        data = payload.get('data') or {}
        purchase.status = TicketPurchase.Status.PENDING
        purchase.gateway_access_code = data.get('access_code', '')
        purchase.gateway_checkout_url = data.get('authorization_url', '')
        purchase.gateway_response = payload
        purchase.save(
            update_fields=[
                'status',
                'gateway_access_code',
                'gateway_checkout_url',
                'gateway_response',
            ]
        )

        if wants_json:
            return JsonResponse(
                {
                    'status': 'success',
                    'access_code': purchase.gateway_access_code,
                    'reference': purchase.gateway_reference,
                    'checkout_url': purchase.gateway_checkout_url,
                    'callback_url': reverse('payments:paystack_callback') + f'?reference={purchase.gateway_reference}&status=success',
                    'amount': float(purchase.amount),
                    'buyer_handling_fee': float(purchase.buyer_handling_fee),
                    'subtotal': float(purchase.amount - purchase.buyer_handling_fee),
                    'currency': purchase.currency,
                    'email': purchase.buyer_email,
                    'buyer_name': purchase.buyer_name,
                    'ticket_type': ticket_type.name,
                    'quantity': purchase.quantity,
                    'event_title': event.title,
                }
            )

        return redirect(purchase.gateway_checkout_url)


class PublicTicketPurchaseDetailView(TemplateView):
    template_name = 'ticketing/purchase_detail.html'

    def get_purchase(self):
        return get_object_or_404(
            TicketPurchase.objects.select_related('event', 'ticket_type').prefetch_related('tickets'),
            gateway_reference=self.kwargs['reference'],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        purchase = self.get_purchase()
        context.update(
            {
                'ticket_purchase': purchase,
                'ticket_status': build_ticket_purchase_status(purchase),
                'tickets': purchase.tickets.all(),
                'status_panel_url': reverse('ticketing:purchase_status_panel', args=[purchase.gateway_reference]),
            }
        )
        return context


class TicketPurchaseStatusPanelView(PublicTicketPurchaseDetailView):
    template_name = 'ticketing/_purchase_status.html'


class PublicTicketDetailView(DetailView):
    model = Ticket
    template_name = 'ticketing/ticket_detail.html'
    context_object_name = 'ticket'

    def get_object(self, queryset=None):
        return get_object_or_404(
            Ticket.objects.select_related('event', 'ticket_type', 'purchase'),
            code__iexact=self.kwargs['code'],
        )


class PublicTicketQrView(View):
    def get(self, request, *args, **kwargs):
        ticket = get_object_or_404(Ticket, code__iexact=kwargs['code'])
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError:
            return HttpResponse(ticket.qr_data, content_type='text/plain')

        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(ticket.qr_data, image_factory=factory)
        response = HttpResponse(content_type='image/svg+xml')
        img.save(response)
        return response


class DashboardTicketedEventsListView(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'dashboard/ticketing/event_list.html'
    context_object_name = 'events'
    paginate_by = 9

    def get_queryset(self):
        return (
            Event.objects.filter(owner=self.request.user, kind=Event.Kind.TICKETED_EVENT)
            .annotate(
                ticket_count=Count('tickets', distinct=True),
                checked_in_count=Count('tickets', filter=Q(tickets__status=Ticket.Status.USED), distinct=True),
                ticket_revenue=Coalesce(
                    Sum('ticket_purchases__amount', filter=Q(ticket_purchases__status=TicketPurchase.Status.PAID)),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
                starting_price=Min('ticket_types__price', filter=Q(ticket_types__is_active=True)),
            )
            .order_by('-created_at')
        )


class DashboardTicketedEventCreateView(SafeIntegrityMixin, LoginRequiredMixin, CreateView):
    model = Event
    form_class = TicketedEventForm
    template_name = 'dashboard/ticketing/event_form.html'

    def form_valid(self, form):
        form.instance.owner = self.request.user
        form.instance.kind = Event.Kind.TICKETED_EVENT
        form.instance.status = Event.Status.DRAFT
        form.instance.vote_price = None
        form.instance.platform_commission_percent = None
        response = super().form_valid(form)
        messages.success(self.request, 'Ticketed event created. Add ticket types before publishing.')
        return response

    def get_success_url(self):
        return self.object.get_dashboard_url()


class DashboardTicketedEventUpdateView(SafeIntegrityMixin, OrganizerTicketEventMixin, UpdateView):
    model = Event
    form_class = TicketedEventForm
    template_name = 'dashboard/ticketing/event_form.html'

    def get_object(self, queryset=None):
        event = self.get_event()
        if event.kind != Event.Kind.TICKETED_EVENT:
            raise Http404('This is not a standalone ticketed event.')
        return event

    def form_valid(self, form):
        previous_end_at = Event.objects.values_list('end_at', flat=True).get(pk=form.instance.pk)
        response = super().form_valid(form)
        if previous_end_at != self.object.end_at:
            TicketScannerPass.objects.filter(event=self.object).update(
                expires_at=scanner_pass_default_expiry(self.object),
                updated_at=timezone.now(),
            )
        return response

    def get_success_url(self):
        return self.object.get_dashboard_url()


class DashboardTicketedEventDetailView(OrganizerTicketEventMixin, DetailView):
    model = Event
    template_name = 'dashboard/ticketing/event_detail.html'
    context_object_name = 'event'

    def get_object(self, queryset=None):
        event = self.get_event()
        if event.kind != Event.Kind.TICKETED_EVENT:
            raise Http404('This is not a standalone ticketed event.')
        return event

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        purchases = TicketPurchase.objects.filter(event=event).select_related('ticket_type').order_by('-initiated_at')[:10]
        totals = TicketPurchase.objects.filter(event=event, status=TicketPurchase.Status.PAID).aggregate(
            total_tickets=Coalesce(Sum('quantity'), Value(0)),
            total_revenue=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        publish_ready, publish_errors = event.can_publish()
        context.update(
            {
                'ticket_types': event.ticket_types.annotate_sold_count(),
                'latest_purchases': purchases,
                'summary': totals,
                'checked_in_count': event.tickets.filter(status=Ticket.Status.USED).count(),
                'publish_ready': publish_ready,
                'publish_errors': publish_errors,
                'ticket_commission_locked': event.ticket_commission_is_locked(),
            }
        )
        return context


class DashboardTicketedEventActionView(OrganizerTicketEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        if event.kind != Event.Kind.TICKETED_EVENT:
            raise Http404('This is not a standalone ticketed event.')
        action = kwargs['action']
        try:
            if action == 'publish':
                event.publish()
                messages.success(request, 'Ticketed event published.')
            elif action == 'unpublish':
                event.unpublish()
                messages.success(request, 'Ticketed event moved back to draft.')
            elif action == 'close':
                event.close()
                messages.success(request, 'Ticketed event closed.')
            else:
                raise Http404('Unknown action.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return HttpResponseRedirect(event.get_dashboard_url())


class DashboardTicketTypeCreateView(SafeIntegrityMixin, OrganizerTicketEventMixin, CreateView):
    model = TicketType
    form_class = TicketTypeForm
    template_name = 'dashboard/ticketing/ticket_type_form.html'
    integrity_error_message = 'A ticket type with this name already exists for this event.'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.get_event()
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Ticket type saved.')
        return response

    def get_success_url(self):
        return self.get_event().get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.get_event()
        return context


class DashboardTicketTypeUpdateView(SafeIntegrityMixin, OrganizerTicketEventMixin, UpdateView):
    model = TicketType
    form_class = TicketTypeForm
    template_name = 'dashboard/ticketing/ticket_type_form.html'
    integrity_error_message = 'A ticket type with this name already exists for this event.'

    def get_object(self, queryset=None):
        return get_object_or_404(TicketType, pk=self.kwargs['pk'], event=self.get_event())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.get_event()
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Ticket type updated.')
        return response

    def get_success_url(self):
        return self.get_event().get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.get_event()
        return context


class DashboardTicketTypeDeleteView(OrganizerTicketEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        ticket_type = get_object_or_404(TicketType, pk=self.kwargs['pk'], event=event)
        if ticket_type.quantity_sold:
            ticket_type.is_active = False
            ticket_type.save(update_fields=['is_active', 'updated_at'])
            messages.warning(request, 'Ticket type has sales, so it was deactivated instead of deleted.')
        else:
            ticket_type.delete()
            messages.success(request, 'Ticket type deleted.')
        return HttpResponseRedirect(event.get_dashboard_url())


class DashboardTicketSalesView(OrganizerTicketEventMixin, ListView):
    model = TicketPurchase
    template_name = 'dashboard/ticketing/sales.html'
    context_object_name = 'purchases'
    paginate_by = 25

    def get_queryset(self):
        return (
            TicketPurchase.objects.filter(event=self.get_event())
            .select_related('ticket_type')
            .prefetch_related('tickets')
            .order_by('-initiated_at')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.get_event()
        return context


class DashboardTicketAttendeesView(OrganizerTicketEventMixin, ListView):
    model = TicketPurchase
    template_name = 'dashboard/ticketing/attendees.html'
    context_object_name = 'purchases'
    paginate_by = 25

    def get_queryset(self):
        query = (self.request.GET.get('q') or '').strip()
        queryset = (
            TicketPurchase.objects.filter(event=self.get_event(), status=TicketPurchase.Status.PAID)
            .select_related('ticket_type')
            .prefetch_related('tickets')
            .annotate(
                ticket_count=Count('tickets', distinct=True),
                checked_in_count=Count('tickets', filter=Q(tickets__status=Ticket.Status.USED), distinct=True),
            )
            .order_by('-completed_at', '-initiated_at')
        )
        if query:
            queryset = queryset.filter(
                Q(buyer_name__icontains=query)
                | Q(buyer_email__icontains=query)
                | Q(buyer_phone__icontains=query)
                | Q(gateway_reference__icontains=query)
                | Q(ticket_type__name__icontains=query)
                | Q(tickets__code__icontains=query)
            ).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.get_event()
        totals = event.tickets.filter(purchase__status=TicketPurchase.Status.PAID).aggregate(
            total=Count('id'),
            checked_in=Count('id', filter=Q(status=Ticket.Status.USED)),
            active=Count('id', filter=Q(status=Ticket.Status.ACTIVE)),
        )
        context.update(
            {
                'event': event,
                'query': (self.request.GET.get('q') or '').strip(),
                'total_ticket_count': totals['total'] or 0,
                'checked_in_count': totals['checked_in'] or 0,
                'active_ticket_count': totals['active'] or 0,
                'provisional_confirmed_count': event.ticket_provisional_entries.filter(status=TicketProvisionalEntry.Status.CONFIRMED).count(),
                'provisional_rejected_count': event.ticket_provisional_entries.filter(status=TicketProvisionalEntry.Status.REJECTED).count(),
            }
        )
        return context


class DashboardTicketCheckInLaunchView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/ticketing/check_in_launch.html'

    def get_queryset(self):
        return Event.objects.filter(owner=self.request.user, kind__in=OrganizerTicketEventMixin.allowed_kinds).order_by('-start_at', 'title')

    def post(self, request, *args, **kwargs):
        event_key = (request.POST.get('event_id') or '').strip()
        normalized_event_key = normalize_event_code(event_key)
        if not event_key:
            messages.error(request, 'Enter an event code or slug.')
            return redirect('dashboard:ticket_check_in_launch')

        events = self.get_queryset()
        lookup = Q(public_code__iexact=event_key) | Q(slug__iexact=event_key)
        if normalized_event_key:
            lookup |= Q(public_code__iexact=normalized_event_key)
            if normalized_event_key.startswith('V') and len(normalized_event_key) > 1:
                lookup |= Q(public_code__iexact=f'V-{normalized_event_key[1:]}')
        if event_key.isdigit():
            lookup |= Q(id=int(event_key))
        event = events.filter(lookup).first()
        if not event:
            messages.error(request, 'No ticketed event was found for that code.')
            return redirect('dashboard:ticket_check_in_launch')
        return redirect('dashboard:ticket_check_in', event.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['events'] = self.get_queryset()[:12]
        return context


class DashboardTicketCheckInView(OrganizerTicketEventMixin, TemplateView):
    template_name = 'dashboard/ticketing/check_in.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.get_event()
        doorlist = ticket_doorlist_for_event(event)
        context['event'] = event
        context['scan_url'] = reverse('dashboard:ticket_check_in_scan', args=[event.slug])
        context['provisional_sync_url'] = reverse('dashboard:ticket_check_in_provisional_sync', args=[event.slug])
        context['provisional_allowed'] = True
        context['event_check_in_code'] = event.public_code
        context['doorlist'] = doorlist
        context['checked_in_count'] = sum(1 for ticket in doorlist if ticket['status'] == Ticket.Status.USED)
        context['active_ticket_count'] = sum(1 for ticket in doorlist if ticket['status'] == Ticket.Status.ACTIVE)
        return context


class DashboardTicketCheckInScanView(OrganizerTicketEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        code = request.POST.get('code')
        if not code and request.headers.get('Content-Type', '').startswith('application/json'):
            import json

            try:
                payload = json.loads(request.body.decode('utf-8'))
            except json.JSONDecodeError:
                payload = {}
            code = payload.get('code')

        result = check_in_ticket(
            event=event,
            code=code,
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return JsonResponse(result, status=200 if result.get('ok') else 400)


class DashboardTicketCheckInProvisionalSyncView(OrganizerTicketEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        attempts = provisional_attempts_from_request(request)
        if not attempts:
            return JsonResponse({'ok': False, 'message': 'No provisional attempts were provided.'}, status=400)
        results = [
            sync_provisional_entry(
                event=event,
                attempt=attempt,
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR') or None,
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
            for attempt in attempts
        ]
        return JsonResponse({'ok': True, 'results': results})


class DashboardTicketScannerPassListView(OrganizerTicketEventMixin, TemplateView):
    template_name = 'dashboard/ticketing/scanner_passes.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.get_event()
        passes = (
            TicketScannerPass.objects.filter(event=event)
            .annotate(
                checkin_count=Count('checkins__ticket', filter=Q(checkins__status_after=Ticket.Status.USED), distinct=True),
                attempt_count=Count('checkins'),
                provisional_confirmed_count=Count(
                    'provisional_entries',
                    filter=Q(provisional_entries__status='confirmed'),
                    distinct=True,
                ),
                provisional_rejected_count=Count(
                    'provisional_entries',
                    filter=Q(provisional_entries__status='rejected'),
                    distinct=True,
                ),
            )
            .order_by('gate_name', 'staff_label', '-created_at')
        )
        from django.core.paginator import Paginator
        paginator = Paginator(passes, 10)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context.update(
            {
                'event': event,
                'scanner_passes': page_obj.object_list,
                'page_obj': page_obj,
                'is_paginated': page_obj.has_other_pages(),
                'form': TicketScannerPassForm(event=event, created_by=self.request.user),
                'credential_reset_form': TicketScannerCredentialResetForm(),
                'now': timezone.now(),
            }
        )
        share_payload = self.request.session.pop(SCANNER_SHARE_SESSION_KEY, None)
        if share_payload and share_payload.get('event_id') == event.pk:
            context['share_panel'] = share_payload
        for scanner_pass in page_obj.object_list:
            scanner_pass.share_url = self.request.build_absolute_uri(scanner_pass.get_absolute_url())
        return context

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        form = TicketScannerPassForm(request.POST, event=event, created_by=request.user)
        if form.is_valid():
            pin = form.cleaned_data['pin']
            scanner_pass = form.save()
            stash_scanner_share_payload(request, scanner_pass, pin)
            messages.success(request, f'Scanner pass created for {scanner_pass.gate_name}.')
            return redirect('dashboard:ticket_scanner_passes', event.slug)
        context = self.get_context_data()
        context['form'] = form
        return self.render_to_response(context)


class DashboardTicketScannerPassActionView(OrganizerTicketEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        scanner_pass = get_object_or_404(TicketScannerPass, pk=kwargs['pk'], event=event)
        action = kwargs['action']
        if action == 'revoke':
            scanner_pass.status = TicketScannerPass.Status.REVOKED
            scanner_pass.revoked_at = timezone.now()
            scanner_pass.device_session_key = ''
            scanner_pass.save(update_fields=['status', 'revoked_at', 'device_session_key', 'updated_at'])
            messages.success(request, 'Scanner pass revoked.')
        elif action == 'reset':
            scanner_pass.device_session_key = ''
            scanner_pass.device_user_agent = ''
            scanner_pass.device_ip = None
            scanner_pass.activated_at = None
            scanner_pass.save(
                update_fields=[
                    'device_session_key',
                    'device_user_agent',
                    'device_ip',
                    'activated_at',
                    'updated_at',
                ]
            )
            messages.success(request, 'Scanner pass device binding reset.')
        elif action == 'toggle_provisional':
            scanner_pass.allow_provisional_entry = not scanner_pass.allow_provisional_entry
            scanner_pass.save(update_fields=['allow_provisional_entry', 'updated_at'])
            state = 'enabled' if scanner_pass.allow_provisional_entry else 'disabled'
            messages.success(request, f'Emergency provisional entry {state} for {scanner_pass.gate_name}.')
        elif action == 'reset_credentials':
            form = TicketScannerCredentialResetForm(request.POST)
            if not form.is_valid():
                messages.error(request, 'Enter a valid new PIN between 4 and 12 characters.')
                return redirect('dashboard:ticket_scanner_passes', event.slug)
            pin = form.cleaned_data['pin']
            scanner_pass.token = TicketScannerPass.generate_unique_token()
            scanner_pass.pin_hash = make_password(pin)
            scanner_pass.status = TicketScannerPass.Status.ACTIVE
            scanner_pass.revoked_at = None
            scanner_pass.expires_at = scanner_pass_default_expiry(event)
            scanner_pass.device_session_key = ''
            scanner_pass.device_user_agent = ''
            scanner_pass.device_ip = None
            scanner_pass.activated_at = None
            scanner_pass.save(
                update_fields=[
                    'token',
                    'pin_hash',
                    'status',
                    'revoked_at',
                    'expires_at',
                    'device_session_key',
                    'device_user_agent',
                    'device_ip',
                    'activated_at',
                    'updated_at',
                ]
            )
            stash_scanner_share_payload(request, scanner_pass, pin)
            messages.success(request, 'Scanner credentials reset. Share the new link and PIN with staff.')
        else:
            raise Http404('Unknown scanner pass action.')
        return redirect('dashboard:ticket_scanner_passes', event.slug)


class PublicTicketScannerPassMixin:
    def get_scanner_pass(self):
        if not hasattr(self, '_scanner_pass'):
            self._scanner_pass = get_object_or_404(
                TicketScannerPass.objects.select_related('event'),
                token=self.kwargs['token'],
            )
        return self._scanner_pass

    def scanner_is_usable(self, scanner_pass):
        return scanner_pass.can_activate(now=timezone.now())

    def session_is_activated(self, request, scanner_pass):
        session_key = request.session.session_key
        return (
            self.scanner_is_usable(scanner_pass)
            and request.session.get(scanner_session_key(scanner_pass)) == scanner_pass.token
            and scanner_pass.is_device_bound_to(session_key)
        )


class PublicTicketScannerPassView(PublicTicketScannerPassMixin, TemplateView):
    template_name = 'ticketing/scanner.html'
    activation_template_name = 'ticketing/scanner_activate.html'
    unavailable_template_name = 'ticketing/scanner_unavailable.html'

    def get_template_names(self):
        scanner_pass = self.get_scanner_pass()
        if not self.scanner_is_usable(scanner_pass):
            return [self.unavailable_template_name]
        if not self.session_is_activated(self.request, scanner_pass):
            return [self.activation_template_name]
        return [self.template_name]

    def post(self, request, *args, **kwargs):
        scanner_pass = self.get_scanner_pass()
        if not self.scanner_is_usable(scanner_pass):
            return self.render_to_response(self.get_context_data(), status=403)

        if is_rate_limited(request, f'ticket-scanner-pin:{scanner_pass.pk}', 5, 300):
            messages.error(request, 'Too many PIN attempts. Wait a few minutes, then try again.')
            return self.render_to_response(self.get_context_data(), status=429)

        form = TicketScannerActivationForm(request.POST)
        if not form.is_valid() or not check_password(form.cleaned_data.get('pin', ''), scanner_pass.pin_hash):
            messages.error(request, 'Invalid scanner PIN.')
            context = self.get_context_data()
            context['form'] = form
            return self.render_to_response(context, status=403)

        session_key = ensure_session_key(request)
        if scanner_pass.device_session_key and scanner_pass.device_session_key != session_key:
            messages.error(request, 'This scanner pass is already active on another device. Ask the organizer to reset it.')
            return self.render_to_response(self.get_context_data(), status=403)

        scanner_pass.device_session_key = session_key
        scanner_pass.device_user_agent = request.META.get('HTTP_USER_AGENT', '')
        scanner_pass.device_ip = request.META.get('REMOTE_ADDR') or None
        scanner_pass.activated_at = scanner_pass.activated_at or timezone.now()
        scanner_pass.save(
            update_fields=[
                'device_session_key',
                'device_user_agent',
                'device_ip',
                'activated_at',
                'updated_at',
            ]
        )
        request.session[scanner_session_key(scanner_pass)] = scanner_pass.token
        messages.success(request, 'Scanner activated for this device.')
        return redirect(scanner_pass.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scanner_pass = self.get_scanner_pass()
        event = scanner_pass.event
        doorlist = ticket_doorlist_for_event(event) if self.session_is_activated(self.request, scanner_pass) else []
        context.update(
            {
                'scanner_pass': scanner_pass,
                'event': event,
                'form': TicketScannerActivationForm(),
                'doorlist': doorlist,
                'scan_url': reverse('ticketing:scanner_pass_scan', args=[scanner_pass.token]),
                'provisional_sync_url': reverse('ticketing:scanner_pass_provisional_sync', args=[scanner_pass.token]),
                'provisional_allowed': scanner_pass.allow_provisional_entry,
                'event_check_in_code': event.public_code,
                'checked_in_count': sum(1 for ticket in doorlist if ticket['status'] == Ticket.Status.USED),
                'active_ticket_count': sum(1 for ticket in doorlist if ticket['status'] == Ticket.Status.ACTIVE),
            }
        )
        return context


class PublicTicketScannerPassScanView(PublicTicketScannerPassMixin, View):
    def post(self, request, *args, **kwargs):
        scanner_pass = self.get_scanner_pass()
        if not self.session_is_activated(request, scanner_pass):
            return JsonResponse({'ok': False, 'message': 'Scanner pass is not active on this device.'}, status=403)

        code = request.POST.get('code')
        if not code and request.headers.get('Content-Type', '').startswith('application/json'):
            try:
                payload = json.loads(request.body.decode('utf-8'))
            except json.JSONDecodeError:
                payload = {}
            code = payload.get('code')

        result = check_in_ticket(
            event=scanner_pass.event,
            code=code,
            scanner_pass=scanner_pass,
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return JsonResponse(result, status=200 if result.get('ok') else 400)


def provisional_attempts_from_request(request):
    payload = {}
    if request.headers.get('Content-Type', '').startswith('application/json'):
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            payload = {}
    attempts = payload.get('attempts') if isinstance(payload, dict) else None
    if isinstance(attempts, list):
        return [attempt for attempt in attempts if isinstance(attempt, dict)]
    return []


class PublicTicketScannerPassProvisionalSyncView(PublicTicketScannerPassMixin, View):
    def post(self, request, *args, **kwargs):
        scanner_pass = self.get_scanner_pass()
        if not self.session_is_activated(request, scanner_pass):
            return JsonResponse({'ok': False, 'message': 'Scanner pass is not active on this device.'}, status=403)
        if not scanner_pass.allow_provisional_entry:
            return JsonResponse({'ok': False, 'message': 'Emergency provisional entry is not enabled for this gate.'}, status=403)
        attempts = provisional_attempts_from_request(request)
        if not attempts:
            return JsonResponse({'ok': False, 'message': 'No provisional attempts were provided.'}, status=400)
        results = [
            sync_provisional_entry(
                event=scanner_pass.event,
                attempt=attempt,
                scanner_pass=scanner_pass,
                ip_address=request.META.get('REMOTE_ADDR') or None,
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
            for attempt in attempts
        ]
        return JsonResponse({'ok': True, 'results': results})
