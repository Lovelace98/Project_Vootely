from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, IntegerField, Min, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView
from django.utils import timezone

from votecentral.mixins import SafeIntegrityMixin
from votecentral.public_urls import build_public_url
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from nominees.models import CompetitionCategory, NominationSubmission, Nominee

from .forms import ContactInquiryForm, EventForm, VoteBundleForm
from .models import Event, VoteBundle
from .performance import build_event_leaderboard, dashboard_home_context
from .blog_data import BLOG_POSTS


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


class LandingPageContextMixin:
    template_name = 'events/landing.html'

    reviews = [
        {
            'quote': 'Vootely made our annual awards competition far easier to run. Students could buy votes quickly, the leaderboard stayed transparent, and the usual complaints dropped significantly.',
            'name': 'Financial Lawrence',
            'title': 'Financial Secretary, Business School, KNUST',
            'segment': 'Student body',
        },
        {
            'quote': 'We used to struggle to confirm who had actually paid for votes. With Vootely, the process felt organized from the start, and our records stayed accurate without the usual stress.',
            'name': 'Lovelace Gyamfi',
            'title': 'Financial Secretary, MELTSA, KNUST',
            'segment': 'Student body',
        },
        {
            'quote': 'Our members are spread across many regions, so we needed a voting platform we could trust. Vootely gave us a stable system, clean administration, and confidence that every transaction was properly accounted for.',
            'name': 'Jeffren Kane',
            'title': 'General Secretary, National Concerned Small Scale Miners Association',
            'segment': 'Organization',
        },
        {
            'quote': 'We needed a simple but secure way to run internal polls and elections. Vootely kept the experience easy for voters while helping our team stay informed throughout the process.',
            'name': 'Kofi Asihene',
            'title': 'Coordinator, Blueprint DNA',
            'segment': 'Organization',
        },
        {
            'quote': 'For internal voting, reliability matters a lot to us. Vootely gave us peace of mind with a dependable process, strong data integrity, and a platform our team could trust.',
            'name': 'Isaac Ampomah Duah',
            'title': 'Manager, Techfortune Ghana',
            'segment': 'Corporate',
        },
    ]
    faq_items = [
        {
            'question': 'What is Vootely used for?',
            'answer': 'Vootely helps organizers run paid public competitions and secure internal elections from one platform. It covers setup, public voting pages, election access, notifications, and organizer-side reporting.',
        },
        {
            'question': 'What is the difference between paid competitions and secure elections?',
            'answer': 'Paid competitions are public-facing campaigns where supporters buy votes for nominees. Secure elections are structured ballots for associations, departments, teams, and organizations that need controlled voter access and a more formal process.',
        },
        {
            'question': 'How does paid voting work on Vootely?',
            'answer': 'Organizers publish an event, nominees receive public voting pages, and supporters pay per vote through Paystack. Votes are counted only after payment confirmation is received.',
        },
        {
            'question': 'Does Vootely support offline USSD voting?',
            'answer': 'Yes. For paid competitions, supporters can vote offline without internet or smartphones. Every nominee receives a unique 5-character voting code. Voters simply dial our platform shortcode, enter the nominee code and quantity, and complete the payment via a direct mobile money prompt on their screen.',
        },
        {
            'question': 'How do secure elections work on Vootely?',
            'answer': 'Organizers set positions and candidates, upload the voter roster, issue voter credentials, and then open the election. Voters use their credential link or token to access the ballot and cast their vote privately.',
        },
        {
            'question': 'Do voters or supporters need to create accounts?',
            'answer': 'No. Paid-voting supporters can vote as guests, and election voters access the ballot through their credential flow. Organizer accounts are only needed for the teams managing events.',
        },
        {
            'question': 'When do votes count?',
            'answer': 'For paid competitions, votes count after successful payment confirmation. For secure elections, ballots count once the voter submits a valid ballot while the election is open.',
        },
        {
            'question': 'How do organizer payouts work?',
            'answer': 'Organizer earnings are tracked in the dashboard after confirmed vote payments. Withdrawal requests are reviewed before payout, and only the organizer net earnings are available to withdraw.',
        },
        {
            'question': 'How do platform fees work for paid competitions?',
            'answer': 'Paid competitions do not use one fixed public commission. Each event has its own agreed platform commission, and that commission must be set before the event can go live.',
        },
        {
            'question': 'How is pricing handled for secure elections?',
            'answer': 'Secure elections use custom election pricing based on the election setup and scope. It is flexible, not a flat one-size-fits-all public fee.',
        },
        {
            'question': 'Can Vootely help us prepare and launch the event?',
            'answer': 'Yes. If you are planning a competition or an election, you can contact Vootely for setup guidance, launch planning, and the right structure for your voting use case.',
        },
        {
            'question': 'Does Vootely support event ticketing?',
            'answer': 'Yes. Vootely lets organizers create ticketed events alongside paid competitions and secure elections. You can set up ticket types (e.g., General Admission, VIP), set pricing, manage sales, and check attendees in at the door — all from the same dashboard.',
        },
        {
            'question': 'How does attendee check-in work for ticketed events?',
            'answer': 'Vootely includes a dedicated scanner tool that works on any smartphone. Create scanner passes for each gate or entry point, and your staff can check attendees in by scanning or searching from their own phone. Check-in data updates in real time so you always know how many guests are inside.',
        },
        {
            'question': 'Can I run a paid competition and sell tickets for the same event?',
            'answer': 'Yes. Vootely supports both in one event. You can sell tickets for entry while also running a paid competition with voting — all managed from a single dashboard with unified reporting.',
        },
    ]

    @staticmethod
    def _format_percent(value):
        quantized = value.quantize(Decimal('0.1'))
        if quantized == quantized.to_integral():
            return str(int(quantized))
        return format(quantized, 'f').rstrip('0').rstrip('.')

    def get_landing_context(self, **overrides):
        active_events = Event.objects.active_public().select_related('owner').prefetch_related('nominees').annotate(
            starting_price=Min('ticket_types__price', filter=Q(ticket_types__is_active=True))
        )
        featured_events = list(active_events.filter(kind=Event.Kind.PAID_COMPETITION)[:10])
        featured_tickets = list(active_events.filter(kind=Event.Kind.TICKETED_EVENT)[:10])
        combined_events = list(active_events.filter(kind__in=[Event.Kind.PAID_COMPETITION, Event.Kind.TICKETED_EVENT])[:12])
        whatsapp_phone = ''.join(character for character in settings.SUPPORT_PHONE if character.isdigit())
        whatsapp_message = "Hello, I'd like to learn more about Vootely for a competition or election."

        context = {
            'active_events_count': active_events.count(),
            'featured_events': featured_events,
            'featured_tickets': featured_tickets,
            'combined_events': combined_events,
            'reviews': self.reviews,
            'faq_items': self.faq_items,
            'contact_form': overrides.pop('contact_form', ContactInquiryForm()),
            'whatsapp_url': f"https://wa.me/{whatsapp_phone}?{urlencode({'text': whatsapp_message})}",
            'ussd_short_code': getattr(settings, 'USSD_SHORT_CODE', '*920*24#'),
            'blogs': BLOG_POSTS[:3],
        }
        context.update(overrides)
        return context


class LandingPageView(LandingPageContextMixin, TemplateView):
    template_name = 'events/landing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_landing_context())
        return context


class LandingContactInquiryCreateView(LandingPageContextMixin, View):
    template_name = 'events/landing.html'

    def post(self, request, *args, **kwargs):
        form = ContactInquiryForm(request.POST)
        if not form.is_valid():
            context = self.get_landing_context(contact_form=form)
            return render(request, self.template_name, context, status=200)

        inquiry = form.save()
        subject = f'Landing inquiry from {inquiry.name}'
        message = (
            'A new landing page inquiry has been submitted.\n\n'
            f'Name: {inquiry.name}\n'
            f'Email: {inquiry.email}\n'
            f'Phone number: {inquiry.phone_number}\n'
            f'Where they heard about us: {inquiry.get_heard_about_us_display()}\n\n'
            'Message:\n'
            f'{inquiry.message}\n'
        )

        try:
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [settings.SUPPORT_EMAIL],
                fail_silently=False,
            )
        except OSError:
            messages.warning(
                request,
                'Your message has been saved. We could not send the email notification right now, but the inquiry is safely on file.',
            )
        else:
            messages.success(request, 'Your message has been received. We will get back to you soon.')

        return HttpResponseRedirect(f"{reverse('events:landing')}#contact")


class BlogListView(TemplateView):
    template_name = 'events/blog_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['posts'] = BLOG_POSTS
        return context


class BlogDetailView(TemplateView):
    template_name = 'events/blog_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs.get('slug')
        post = next((p for p in BLOG_POSTS if p['slug'] == slug), None)
        if not post:
            raise Http404("Blog post not found")
        
        context['post'] = post
        context['recommended_posts'] = [p for p in BLOG_POSTS if p['slug'] != slug][:3]
        return context


class HomeView(ListView):
    model = Event
    template_name = 'events/home.html'
    context_object_name = 'events'
    paginate_by = 6

    def get_queryset(self):
        queryset = Event.objects.published().select_related('owner').prefetch_related('nominees').annotate(
            starting_price=Min('ticket_types__price', filter=Q(ticket_types__is_active=True))
        )
        
        q = self.request.GET.get('q', '').strip()
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q) | Q(description__icontains=q)
            )

        status = self.request.GET.get('status', 'active')
        now = timezone.now()
        if status == 'active':
            queryset = queryset.filter(start_at__lte=now, end_at__gte=now)
        elif status == 'upcoming':
            queryset = queryset.filter(start_at__gt=now)
        elif status == 'ended':
            queryset = queryset.filter(end_at__lt=now)

        kind = self.request.GET.get('kind', 'all').strip().lower()
        if kind == 'ticketed_event':
            queryset = queryset.filter(kind=Event.Kind.TICKETED_EVENT)
        elif kind == 'paid_competition':
            queryset = queryset.filter(kind=Event.Kind.PAID_COMPETITION)

        sort = self.request.GET.get('sort', 'newest')
        if sort == 'newest':
            queryset = queryset.order_by('-published_at')
        elif sort == 'ending_soon':
            queryset = queryset.order_by('end_at')

        return queryset

    def get_template_names(self):
        if self.request.htmx:
            return ['events/includes/_events_grid.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('q', '')
        context['current_status'] = self.request.GET.get('status', 'active')
        context['current_sort'] = self.request.GET.get('sort', 'newest')
        context['current_kind'] = self.request.GET.get('kind', 'all')
        return context


class EventDetailView(DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(
            kind__in=[Event.Kind.PAID_COMPETITION, Event.Kind.TICKETED_EVENT],
            is_public=True,
            status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        ).select_related('owner').prefetch_related(
            Prefetch(
                'competition_categories',
                queryset=CompetitionCategory.objects.filter(is_active=True).prefetch_related(
                    Prefetch('nominees', queryset=Nominee.objects.filter(is_active=True).order_by('display_order', 'name'))
                ).order_by('display_order', 'name'),
            )
            ,
            'ticket_types',
        )

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
        if event.kind == Event.Kind.PAID_COMPETITION and event.show_leaderboard:
            context['leaderboard'] = build_event_leaderboard(event)
        else:
            context['leaderboard'] = None
            context['leaderboard_hidden'] = event.kind == Event.Kind.PAID_COMPETITION
        context['state'] = event.public_state()
        category_sections = []
        if event.kind == Event.Kind.PAID_COMPETITION:
            for category in event.competition_categories.filter(is_active=True).order_by('display_order', 'name'):
                nominees = list(category.nominees.filter(is_active=True).order_by('display_order', 'name'))
                category_sections.append({'category': category, 'nominees': nominees})
        context['category_sections'] = category_sections
        context['nominees'] = event.nominees.filter(is_active=True).select_related('category') if event.kind == Event.Kind.PAID_COMPETITION else []
        context['nominations_open'] = event.accepts_nominations()
        context['nomination_url'] = (
            build_public_url(reverse('events:nominate', args=[event.slug]))
            if event.allow_public_nominations
            else ''
        )
        ticket_types = list(event.ticket_types.filter(is_active=True).order_by('price', 'name').annotate_sold_count())
        for ticket_type in ticket_types:
            ticket_type.public_is_on_sale = ticket_type.is_on_sale()
        context['ticket_types'] = ticket_types
        context['tickets_open'] = event.accepts_tickets()
        context['ussd_short_code'] = getattr(settings, 'USSD_SHORT_CODE', '*920*24#')
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
        response = super().form_valid(form)
        if self.object.kind == Event.Kind.PAID_COMPETITION:
            from notifications.services import queue_event_commission_setup_required

            queue_event_commission_setup_required(self.object)
        return response

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
                'nominees': event.nominees.select_related('category').all(),
                'categories': event.competition_categories.prefetch_related('nominees').all(),
                'submission_counts': dict(
                    event.nomination_submissions.values('status').annotate(
                        count=Count('pk')
                    ).values_list('status', 'count')
                ),
                'nomination_url': (
                    build_public_url(reverse('events:nominate', args=[event.slug]))
                    if event.allow_public_nominations
                    else ''
                ),
                'live_leaderboard_url': build_public_url(
                    reverse('events:leaderboard_live', args=[event.slug])
                ),
                'commission_locked': event.commission_is_locked(),
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
            | Q(category__name__icontains=query)
        ).select_related('event', 'category')[:6]

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

        from ticketing.models import Ticket, TicketPurchase

        ticket_purchases = TicketPurchase.objects.filter(
            event__owner=request.user
        ).filter(
            Q(gateway_reference__icontains=query)
            | Q(buyer_name__icontains=query)
            | Q(buyer_email__icontains=query)
            | Q(buyer_phone__icontains=query)
            | Q(event__title__icontains=query)
            | Q(ticket_type__name__icontains=query)
        ).select_related('event', 'ticket_type')[:6]

        tickets = Ticket.objects.filter(
            event__owner=request.user
        ).filter(
            Q(code__icontains=query)
            | Q(purchase__gateway_reference__icontains=query)
            | Q(purchase__buyer_email__icontains=query)
            | Q(event__title__icontains=query)
        ).select_related('event', 'ticket_type', 'purchase')[:6]

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
            'ticket_purchases': ticket_purchases,
            'tickets': tickets,
            'query': query,
        }
        return render(request, self.template_name, context)


class DashboardCompetitionsListView(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'dashboard/events/list.html'
    context_object_name = 'events'
    paginate_by = 6

    def get_queryset(self):
        from .performance import competition_events_queryset
        return competition_events_queryset(self.request.user).prefetch_related('nominees', 'competition_categories').order_by('-start_at')

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


class DashboardVoteBundleCreateView(OrganizerEventMixin, CreateView):
    model = VoteBundle
    form_class = VoteBundleForm
    template_name = 'dashboard/events/bundle_form.html'

    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('owner'),
                slug=self.kwargs['event_slug'],
                owner=self.request.user,
                kind=Event.Kind.PAID_COMPETITION,
            )
        return self._event

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.get_event()
        return kwargs

    def form_valid(self, form):
        form.instance.event = self.get_event()
        response = super().form_valid(form)
        messages.success(self.request, 'Vote bundle added successfully.')
        return response

    def get_success_url(self):
        return self.get_event().get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.get_event()
        return context


class DashboardVoteBundleDeleteView(OrganizerEventMixin, View):
    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('owner'),
                slug=self.kwargs['event_slug'],
                owner=self.request.user,
                kind=Event.Kind.PAID_COMPETITION,
            )
        return self._event

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        bundle = get_object_or_404(VoteBundle, event=event, pk=self.kwargs['pk'])
        bundle.delete()
        messages.success(request, 'Vote bundle deleted.')
        return HttpResponseRedirect(event.get_dashboard_url())


class EventLiveLeaderboardView(DetailView):
    model = Event
    template_name = 'events/live_leaderboard.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(
            kind=Event.Kind.PAID_COMPETITION,
            is_public=True,
            status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        ).select_related('owner')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        from .performance import build_event_leaderboard
        if event.show_leaderboard:
            context['leaderboard'] = build_event_leaderboard(event)
        else:
            context['leaderboard'] = None
            context['leaderboard_hidden'] = True
        return context
