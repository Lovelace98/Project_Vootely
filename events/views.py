from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from votecentral.mixins import SafeIntegrityMixin
from payments.models import PaymentAttempt
from votes.models import VotePurchase

from .forms import EventForm
from .models import Event
from .performance import build_event_leaderboard, dashboard_home_context


class OrganizerEventMixin(LoginRequiredMixin):
    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('owner'),
                slug=self.kwargs['slug'],
                owner=self.request.user,
                kind=Event.Kind.PAID_COMPETITION,
            )
        return self._event


class HomeView(ListView):
    model = Event
    template_name = 'events/home.html'
    context_object_name = 'events'

    def get_queryset(self):
        return Event.objects.active_public().select_related('owner').prefetch_related('nominees')


class EventDetailView(DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(
            kind=Event.Kind.PAID_COMPETITION,
            is_public=True,
            status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        ).select_related('owner').prefetch_related('nominees')

    def get(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            slug = self.kwargs.get('slug')
            event_exists = Event.objects.filter(slug=slug).first()
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
        event = self.object
        if event.show_leaderboard:
            context['leaderboard'] = build_event_leaderboard(event)
        else:
            context['leaderboard'] = None
            context['leaderboard_hidden'] = True
        context['state'] = event.public_state()
        context['nominees'] = event.nominees.filter(is_active=True)
        return context


class EventLeaderboardPartialView(DetailView):
    model = Event
    template_name = 'events/_leaderboard.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(
            kind=Event.Kind.PAID_COMPETITION,
            is_public=True,
            status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.object.show_leaderboard:
            context['leaderboard'] = build_event_leaderboard(self.object)
        else:
            context['leaderboard'] = None
            context['leaderboard_hidden'] = True
        return context


class DashboardHomeView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/events/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            dashboard_home_context(
                self.request.user,
                timeframe=self.request.GET.get('timeframe', 'this_month'),
                event_slug=self.request.GET.get('event', ''),
            )
        )
        return context


class DashboardEventCreateView(SafeIntegrityMixin, LoginRequiredMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = 'dashboard/events/form.html'
    success_message = 'Draft event created.'

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return self.object.get_dashboard_url()


class DashboardEventUpdateView(SafeIntegrityMixin, OrganizerEventMixin, UpdateView):
    model = Event
    form_class = EventForm
    template_name = 'dashboard/events/form.html'
    success_message = 'Event updated.'

    def get_object(self, queryset=None):
        return self.get_event()

    def get_success_url(self):
        return self.object.get_dashboard_url()


class DashboardEventDetailView(OrganizerEventMixin, DetailView):
    model = Event
    template_name = 'dashboard/events/detail.html'
    context_object_name = 'event'

    def get_object(self, queryset=None):
        return self.get_event()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        payments = PaymentAttempt.objects.filter(event=event).order_by('-initiated_at')[:10]
        purchases = VotePurchase.objects.filter(event=event)
        totals = purchases.aggregate(
            total_votes=Coalesce(
                Sum('quantity'),
                Value(0),
                output_field=IntegerField(),
            ),
            total_revenue=Coalesce(
                Sum('amount_paid'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        publish_ready, publish_errors = event.can_publish()
        context.update(
            {
                'leaderboard': build_event_leaderboard(event),
                'latest_payments': payments,
                'summary': totals,
                'publish_ready': publish_ready,
                'publish_errors': publish_errors,
                'nominees': event.nominees.all(),
            }
        )
        return context


class DashboardEventActionView(OrganizerEventMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        action = kwargs['action']
        previous_status = event.status

        try:
            if action == 'publish':
                event.publish()
                if previous_status != Event.Status.PUBLISHED:
                    from notifications.services import queue_event_notification
                    from notifications.models import Notification

                    queue_event_notification(event, Notification.EventType.EVENT_PUBLISHED)
                messages.success(request, 'Event published.')
            elif action == 'unpublish':
                event.unpublish()
                messages.success(request, 'Event moved back to draft.')
            elif action == 'close':
                event.close()
                if previous_status != Event.Status.CLOSED:
                    from notifications.services import queue_event_notification
                    from notifications.models import Notification

                    queue_event_notification(event, Notification.EventType.EVENT_CLOSED)
                messages.success(request, 'Event closed.')
            else:
                raise Http404('Unknown action.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)

        return HttpResponseRedirect(event.get_dashboard_url())


from elections.models import (
    ElectionCandidate,
    ElectionCredential,
    ElectionInvoice,
    ElectionPosition,
    ElectionVoter,
    OrganizerPaymentAttempt,
)
from nominees.models import Nominee

class DashboardSearchView(LoginRequiredMixin, View):
    template_name = 'dashboard/includes/_search_results.html'

    def get(self, request, *args, **kwargs):
        query = request.GET.get('q', '').strip()
        if not query or len(query) < 2:
            return HttpResponse('<p class="text-xs text-vc-dark-300 italic dark:text-slate-400 p-4">Type at least 2 characters to search...</p>')

        events = Event.objects.filter(
            owner=request.user,
        ).filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(slug__icontains=query)
            | Q(status__icontains=query)
            | Q(kind__icontains=query)
        ).order_by('-updated_at')[:8]

        nominees = Nominee.objects.filter(
            event__owner=request.user
        ).filter(
            Q(name__icontains=query)
            | Q(bio__icontains=query)
            | Q(code__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event')[:6]

        election_candidates = ElectionCandidate.objects.filter(
            event__owner=request.user
        ).filter(
            Q(name__icontains=query)
            | Q(bio__icontains=query)
            | Q(position__title__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event', 'position')[:6]

        election_positions = ElectionPosition.objects.filter(
            event__owner=request.user
        ).filter(
            Q(title__icontains=query)
            | Q(slug__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event')[:6]

        election_voters = ElectionVoter.objects.filter(
            event__owner=request.user
        ).filter(
            Q(external_id__icontains=query)
            | Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event')[:6]

        election_credentials = ElectionCredential.objects.filter(
            event__owner=request.user
        ).filter(
            Q(status__icontains=query)
            | Q(voter__external_id__icontains=query)
            | Q(voter__name__icontains=query)
            | Q(voter__email__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event', 'voter')[:6]

        election_invoices = ElectionInvoice.objects.filter(
            event__owner=request.user
        ).filter(
            Q(status__icontains=query)
            | Q(currency__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event')[:6]

        payments = PaymentAttempt.objects.filter(
            event__owner=request.user
        ).filter(
            Q(gateway_reference__icontains=query) |
            Q(voter_name__icontains=query) |
            Q(voter_email__icontains=query) |
            Q(voter_phone__icontains=query) |
            Q(event__title__icontains=query) |
            Q(nominee__name__icontains=query)
        ).select_related('event', 'nominee')[:6]

        organizer_payments = OrganizerPaymentAttempt.objects.filter(
            owner=request.user
        ).filter(
            Q(gateway_reference__icontains=query)
            | Q(payer_email__icontains=query)
            | Q(status__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event', 'invoice')[:6]

        from django.shortcuts import render
        
        context = {
            'events': events,
            'nominees': nominees,
            'election_candidates': election_candidates,
            'election_positions': election_positions,
            'election_voters': election_voters,
            'election_credentials': election_credentials,
            'election_invoices': election_invoices,
            'payments': payments,
            'organizer_payments': organizer_payments,
            'query': query,
        }
        return render(request, self.template_name, context)


class DashboardCompetitionsListView(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'dashboard/events/list.html'
    context_object_name = 'events'
    paginate_by = 6

    def get_queryset(self):
        return Event.objects.filter(
            owner=self.request.user,
            kind=Event.Kind.PAID_COMPETITION,
        ).prefetch_related('nominees').order_by('-start_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['list_type'] = 'competitions'
        context['title'] = 'My Competitions'
        context['subtitle'] = 'Manage, monitor, and publish your voting competitions.'
        return context


class DashboardElectionsListView(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'dashboard/events/list.html'
    context_object_name = 'events'
    paginate_by = 6

    def get_queryset(self):
        return Event.objects.filter(
            owner=self.request.user,
            kind=Event.Kind.SECURE_ELECTION,
        ).prefetch_related('election_positions', 'election_candidates', 'election_voters').order_by('-start_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['list_type'] = 'elections'
        context['title'] = 'Secure Elections'
        context['subtitle'] = 'Configure eligible voters, candidate positions, and secure tallies.'
        return context


class PrivacyPolicyView(TemplateView):
    template_name = 'legal/privacy_policy.html'


class TermsOfServiceView(TemplateView):
    template_name = 'legal/terms_of_service.html'


class OrganizerAgreementView(TemplateView):
    template_name = 'legal/merchant_agreement.html'
