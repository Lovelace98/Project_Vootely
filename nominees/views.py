from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from events.views import build_event_leaderboard
from payments.forms import PaymentInitiationForm
from payments.services import resolve_public_payment_status
from votecentral.mixins import SafeIntegrityMixin

from .forms import NomineeForm
from .models import Nominee


class NomineeEventMixin(LoginRequiredMixin):
    def get_event(self):
        from events.models import Event

        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.filter(owner=self.request.user),
                slug=self.kwargs['event_slug'],
            )
        return self._event

    def get_nominee(self):
        if not hasattr(self, '_nominee'):
            self._nominee = get_object_or_404(
                Nominee.objects.filter(event=self.get_event()),
                slug=self.kwargs['slug'],
            )
        return self._nominee


class PublicNomineeDetailView(DetailView):
    template_name = 'events/nominee_detail.html'
    context_object_name = 'nominee'

    def get_object(self, queryset=None):
        from events.models import Event

        event = get_object_or_404(
            Event.objects.filter(
                kind=Event.Kind.PAID_COMPETITION,
                is_public=True,
                status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
            ),
            slug=self.kwargs['event_slug'],
        )
        try:
            nominee = Nominee.resolve_for_event(event, self.kwargs['nominee_ref'])
        except Nominee.DoesNotExist as exc:
            raise Http404 from exc

        self.event = event
        return nominee

    def get(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            from events.models import Event
            event_slug = self.kwargs.get('event_slug')
            event_exists = Event.objects.filter(slug=event_slug).first()
            active_events = Event.objects.active_public()[:6]
            from django.shortcuts import render
            return render(
                request,
                'events/event_unavailable.html',
                {
                    'event_exists': event_exists,
                    'active_events': active_events,
                },
                status=404
            )
        
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment_reference = self.request.GET.get('payment_reference', '').strip()
        context['event'] = self.event
        context['payment_form'] = PaymentInitiationForm(
            initial={
                'event_slug': self.event.slug,
                'nominee_ref': self.object.slug,
                'quantity': 1,
            }
        )
        context['leaderboard'] = build_event_leaderboard(self.event)
        context['state'] = self.event.public_state()
        context['payment_status'] = resolve_public_payment_status(
            self.event,
            self.object,
            payment_reference,
        )
        context['payment_reference'] = payment_reference
        context['payment_status_poll_url'] = (
            f"{reverse('events:nominee_payment_status', args=[self.event.slug, self.object.slug])}"
            f'?payment_reference={payment_reference}'
            if payment_reference
            else ''
        )
        return context


class PublicNomineePaymentStatusView(TemplateView):
    template_name = 'payments/_payment_status.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from events.models import Event

        event = get_object_or_404(
            Event.objects.filter(
                kind=Event.Kind.PAID_COMPETITION,
                is_public=True,
                status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
            ),
            slug=self.kwargs['event_slug'],
        )
        try:
            nominee = Nominee.resolve_for_event(event, self.kwargs['nominee_ref'])
        except Nominee.DoesNotExist as exc:
            raise Http404 from exc

        payment_reference = self.request.GET.get('payment_reference', '').strip()
        context['payment_status'] = resolve_public_payment_status(
            event,
            nominee,
            payment_reference,
        )
        context['event'] = event
        context['nominee'] = nominee
        context['payment_reference'] = payment_reference
        context['payment_status_poll_url'] = (
            f"{reverse('events:nominee_payment_status', args=[event.slug, nominee.slug])}"
            f'?payment_reference={payment_reference}'
            if payment_reference
            else ''
        )
        return context


class DashboardNomineeCreateView(SafeIntegrityMixin, NomineeEventMixin, CreateView):
    model = Nominee
    form_class = NomineeForm
    template_name = 'dashboard/nominees/form.html'
    success_message = 'Nominee added.'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.event = self.event
        response = super().form_valid(form)
        from notifications.services import create_in_app_notification
        create_in_app_notification(
            user=self.event.owner,
            title="Nominee Registered Successfully",
            message=f"Nominee '{form.instance.name}' has been added to your event '{self.event.title}'.",
            link=self.event.get_dashboard_url(),
            level='success',
            event=self.event,
            nominee=form.instance,
        )
        return response

    def get_success_url(self):
        return self.event.get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        return context


class DashboardNomineeUpdateView(SafeIntegrityMixin, NomineeEventMixin, UpdateView):
    model = Nominee
    form_class = NomineeForm
    template_name = 'dashboard/nominees/form.html'
    success_message = 'Nominee updated.'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.get_nominee()

    def get_success_url(self):
        return self.event.get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        return context


class DashboardNomineeDeleteView(NomineeEventMixin, View):
    def post(self, request, *args, **kwargs):
        nominee = self.get_nominee()
        event = nominee.event
        nominee.delete()
        messages.success(request, 'Nominee deleted.')
        return HttpResponseRedirect(event.get_dashboard_url())
