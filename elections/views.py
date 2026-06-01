import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, TemplateView, UpdateView

from events.models import Event
from votecentral.mixins import SafeIntegrityMixin

from .forms import (
    CredentialTokenForm,
    ElectionCandidateForm,
    ElectionEventForm,
    ElectionPositionForm,
    RosterUploadForm,
)
from .models import (
    BallotReceipt,
    ElectionConfig,
    ElectionCredential,
    ElectionInvoice,
    OrganizerPaymentAttempt,
    ElectionPosition,
    ElectionCandidate,
)
from .services import (
    audit,
    build_tally,
    can_open_election,
    cast_ballot,
    close_election,
    create_organizer_payment_attempt,
    eligible_voter_count,
    generate_invoice,
    generate_tally,
    has_paid_for_current_roster,
    import_roster,
    ensure_setup_can_change,
    lock_election_roster,
    publish_election_results,
    certify_election,
    initialize_organizer_paystack_transaction,
    issue_credentials,
    open_election,
    organizer_payment_status_redirect_url,
    record_organizer_paystack_callback,
    resolve_credential,
    results_are_public,
)


class OrganizerElectionMixin(LoginRequiredMixin):
    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('owner').filter(kind=Event.Kind.SECURE_ELECTION),
                slug=self.kwargs['slug'],
                owner=self.request.user,
            )
        return self._event


class DashboardElectionCreateView(SafeIntegrityMixin, LoginRequiredMixin, CreateView):
    model = Event
    form_class = ElectionEventForm
    template_name = 'dashboard/elections/form.html'
    success_message = 'Draft election created.'

    def form_valid(self, form):
        form.instance.owner = self.request.user
        form.instance.kind = Event.Kind.SECURE_ELECTION
        response = super().form_valid(form)
        ElectionConfig.objects.get_or_create(event=self.object)
        audit(self.object, 'election_created', actor=self.request.user, request=self.request)
        return response

    def get_success_url(self):
        return reverse('dashboard:election_detail', args=[self.object.slug])


class DashboardElectionUpdateView(SafeIntegrityMixin, OrganizerElectionMixin, UpdateView):
    model = Event
    form_class = ElectionEventForm
    template_name = 'dashboard/elections/form.html'
    success_message = 'Election updated.'

    def get_object(self, queryset=None):
        return self.get_event()

    def form_valid(self, form):
        try:
            ensure_setup_can_change(self.get_object())
            response = super().form_valid(form)
            audit(self.object, 'config_updated', actor=self.request.user, request=self.request)
            return response
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('dashboard:election_detail', args=[self.object.slug])


class DashboardElectionDetailView(OrganizerElectionMixin, DetailView):
    model = Event
    template_name = 'dashboard/elections/detail.html'
    context_object_name = 'event'

    def get_object(self, queryset=None):
        return self.get_event()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        can_open, open_errors = can_open_election(event)
        latest_invoice = event.election_invoices.first()
        latest_tally = event.tally_snapshots.first()
        context.update(
            {
                'positions': event.election_positions.prefetch_related('candidates'),
                'voter_count': eligible_voter_count(event),
                'credential_count': event.election_credentials.count(),
                'unused_credential_count': event.election_credentials.filter(
                    status__in=[ElectionCredential.Status.ISSUED, ElectionCredential.Status.OPENED]
                ).count(),
                'ballot_count': event.ballots.count(),
                'latest_invoice': latest_invoice,
                'latest_export': event.credential_exports.first(),
                'latest_tally': latest_tally,
                'can_open': can_open,
                'open_errors': open_errors,
                'paid_for_roster': has_paid_for_current_roster(event),
            }
        )
        return context


class DashboardElectionPositionsView(OrganizerElectionMixin, FormView):
    template_name = 'dashboard/elections/positions.html'
    form_class = ElectionPositionForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            ensure_setup_can_change(self.event)
            position = form.save(commit=False)
            position.event = self.event
            position.save()
            audit(self.event, 'position_created', actor=self.request.user, obj=position, request=self.request)
            messages.success(self.request, 'Position added.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['positions'] = self.event.election_positions.prefetch_related('candidates')
        return context

    def get_success_url(self):
        return reverse('dashboard:election_positions', args=[self.event.slug])


class DashboardElectionCandidatesView(OrganizerElectionMixin, FormView):
    template_name = 'dashboard/elections/candidates.html'
    form_class = ElectionCandidateForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

    def form_valid(self, form):
        try:
            ensure_setup_can_change(self.event)
            candidate = form.save(commit=False)
            candidate.event = self.event
            candidate.save()
            audit(self.event, 'candidate_created', actor=self.request.user, obj=candidate, request=self.request)
            messages.success(self.request, 'Candidate added.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['positions'] = self.event.election_positions.prefetch_related('candidates')
        return context

    def get_success_url(self):
        return reverse('dashboard:election_candidates', args=[self.event.slug])


class DashboardElectionPositionUpdateView(SafeIntegrityMixin, OrganizerElectionMixin, UpdateView):
    model = ElectionPosition
    form_class = ElectionPositionForm

    def get_queryset(self):
        return ElectionPosition.objects.filter(event=self.get_event())

    def form_valid(self, form):
        try:
            ensure_setup_can_change(self.get_event())
            position = form.save()
            audit(self.get_event(), 'position_updated', actor=self.request.user, obj=position, request=self.request)
            messages.success(self.request, 'Position updated.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('dashboard:election_positions', args=[self.get_event().slug])


class DashboardElectionPositionDeleteView(OrganizerElectionMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        try:
            ensure_setup_can_change(event)
            position = get_object_or_404(ElectionPosition, event=event, pk=kwargs['pk'])
            title = position.title
            position.delete()
            audit(event, 'position_deleted', actor=self.request.user, obj_id=kwargs['pk'], metadata={'title': title}, request=request)
            messages.success(request, f'Position "{title}" deleted.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return HttpResponseRedirect(reverse('dashboard:election_positions', args=[event.slug]))


class DashboardElectionCandidateUpdateView(SafeIntegrityMixin, OrganizerElectionMixin, UpdateView):
    model = ElectionCandidate
    form_class = ElectionCandidateForm

    def get_queryset(self):
        return ElectionCandidate.objects.filter(event=self.get_event())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.get_event()
        return kwargs

    def form_valid(self, form):
        try:
            ensure_setup_can_change(self.get_event())
            candidate = form.save()
            audit(self.get_event(), 'candidate_updated', actor=self.request.user, obj=candidate, request=self.request)
            messages.success(self.request, 'Candidate updated.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('dashboard:election_candidates', args=[self.get_event().slug])


class DashboardElectionCandidateDeleteView(OrganizerElectionMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        try:
            ensure_setup_can_change(event)
            candidate = get_object_or_404(ElectionCandidate, event=event, pk=kwargs['pk'])
            name = candidate.name
            candidate.delete()
            audit(event, 'candidate_deleted', actor=self.request.user, obj_id=kwargs['pk'], metadata={'name': name}, request=request)
            messages.success(request, f'Candidate "{name}" deleted.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return HttpResponseRedirect(reverse('dashboard:election_candidates', args=[event.slug]))


class DashboardElectionRosterView(OrganizerElectionMixin, FormView):
    template_name = 'dashboard/elections/roster.html'
    form_class = RosterUploadForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if request.GET.get('download') == 'template':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="votecentral-roster-template.csv"'
            writer = csv.writer(response)
            writer.writerow(['external_id', 'name', 'email', 'phone'])
            writer.writerow(['V001', 'Kwame Mensah', 'kwame@example.com', '0241234567'])
            writer.writerow(['V002', 'Abena Owusu', 'abena@example.com', '0209876543'])
            writer.writerow(['V003', 'John Doe', '', ''])
            return response
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            voters = import_roster(
                self.event,
                form.cleaned_data['roster'],
                actor=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(self.request, message)
            return self.form_invalid(form)
        messages.success(self.request, f'Imported {len(voters)} eligible voters.')
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['voters'] = self.event.election_voters.all()[:100]
        context['voter_count'] = eligible_voter_count(self.event)
        return context

    def get_success_url(self):
        return reverse('dashboard:election_roster', args=[self.event.slug])


class DashboardElectionInvoiceView(OrganizerElectionMixin, TemplateView):
    template_name = 'dashboard/elections/invoice.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        invoice_id = request.POST.get('invoice_id')
        try:
            if action == 'pay':
                if invoice_id:
                    invoice = get_object_or_404(self.event.election_invoices, pk=invoice_id)
                else:
                    invoice = generate_invoice(self.event, actor=request.user, request=request)
                
                if invoice and invoice.status != ElectionInvoice.Status.PAID:
                    attempt = create_organizer_payment_attempt(invoice, owner=request.user)
                    if attempt.status == OrganizerPaymentAttempt.Status.PENDING and attempt.gateway_checkout_url:
                        if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                            return JsonResponse({
                                'status': 'success',
                                'access_code': attempt.gateway_access_code,
                                'reference': attempt.gateway_reference,
                                'checkout_url': attempt.gateway_checkout_url,
                                'callback_url': reverse('payments:paystack_callback') + f'?reference={attempt.gateway_reference}&status=success',
                                'amount': float(attempt.amount),
                                'currency': attempt.currency,
                                'email': attempt.payer_email,
                                'invoice_id': invoice.id,
                                'event_title': self.event.title,
                            })
                        return redirect(attempt.gateway_checkout_url)
                    payload = initialize_organizer_paystack_transaction(attempt)
                    data = payload.get('data') or {}
                    if attempt.status != OrganizerPaymentAttempt.Status.PENDING or not attempt.gateway_checkout_url:
                        attempt.status = OrganizerPaymentAttempt.Status.PENDING
                        attempt.gateway_access_code = data.get('access_code', '')
                        attempt.gateway_checkout_url = data.get('authorization_url', '')
                        attempt.gateway_response = payload
                        attempt.save(
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
                            'access_code': attempt.gateway_access_code,
                            'reference': attempt.gateway_reference,
                            'checkout_url': attempt.gateway_checkout_url,
                            'callback_url': reverse('payments:paystack_callback') + f'?reference={attempt.gateway_reference}&status=success',
                            'amount': float(attempt.amount),
                            'currency': attempt.currency,
                            'email': attempt.payer_email,
                            'invoice_id': invoice.id,
                            'event_title': self.event.title,
                        })
                    return redirect(attempt.gateway_checkout_url)
            else:
                invoice = generate_invoice(self.event, actor=request.user, request=request)
                messages.success(request, 'Election invoice generated.')
        except Exception as exc:
            if request.headers.get('Accept') == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('inline') == 'true':
                return JsonResponse({
                    'status': 'error',
                    'message': str(exc)
                }, status=400)
            messages.error(request, str(exc))
        return redirect('dashboard:election_invoice', slug=self.event.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['voter_count'] = eligible_voter_count(self.event)
        context['invoices'] = self.event.election_invoices.all()
        context['latest_invoice'] = self.event.election_invoices.first()
        context['paid_for_roster'] = has_paid_for_current_roster(self.event)
        return context


class DashboardElectionCredentialsView(OrganizerElectionMixin, TemplateView):
    template_name = 'dashboard/elections/credentials.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            export = issue_credentials(self.event, actor=request.user, request=request)
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return redirect('dashboard:election_credentials', slug=self.event.slug)
        messages.success(request, f'Issued {export.row_count} credentials.')
        return redirect('dashboard:election_credentials', slug=self.event.slug)

    def get(self, request, *args, **kwargs):
        if request.GET.get('download') == 'csv':
            export = self.event.credential_exports.first()
            if not export:
                raise Http404
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{self.event.slug}-credentials.csv"'
            writer = csv.DictWriter(
                response,
                fieldnames=['external_id', 'name', 'email', 'phone', 'token', 'vote_url', 'email_sent'],
            )
            writer.writeheader()
            writer.writerows(export.rows)
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['latest_export'] = self.event.credential_exports.first()
        context['credentials'] = self.event.election_credentials.select_related('voter')[:100]
        return context


class DashboardElectionActionView(OrganizerElectionMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.get_event()
        action = kwargs['action']
        try:
            if action == 'lock_roster':
                lock_election_roster(event, actor=request.user, request=request)
                messages.success(request, 'Roster locked.')
            elif action == 'open':
                open_election(event, actor=request.user, request=request)
                messages.success(request, 'Election opened.')
            elif action == 'close':
                close_election(event, actor=request.user, request=request)
                messages.success(request, 'Election closed.')
            elif action == 'tally':
                if event.status != Event.Status.CLOSED:
                    raise ValidationError('Only a closed election can be tallied.')
                generate_tally(event, actor=request.user, request=request)
                messages.success(request, 'Election tallied.')
            elif action == 'publish_results':
                publish_election_results(event, actor=request.user, request=request)
                messages.success(request, 'Results published.')
            elif action == 'certify':
                certify_election(event, actor=request.user, request=request)
                messages.success(request, 'Election certified.')
            else:
                raise Http404('Unknown action.')
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return HttpResponseRedirect(reverse('dashboard:election_detail', args=[event.slug]))


class PublicElectionDetailView(DetailView):
    model = Event
    template_name = 'elections/detail.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(
            kind=Event.Kind.SECURE_ELECTION,
            is_public=True,
            status__in=[Event.Status.OPEN, Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED],
        ).prefetch_related('election_positions__candidates')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['positions'] = self.object.election_positions.filter(is_active=True).prefetch_related('candidates')
        context['results_public'] = results_are_public(self.object)
        context['latest_tally'] = self.object.tally_snapshots.first()
        return context


class PublicElectionVoteView(TemplateView):
    template_name = 'elections/vote.html'

    def dispatch(self, request, *args, **kwargs):
        self.event = get_object_or_404(
            Event.objects.filter(kind=Event.Kind.SECURE_ELECTION, is_public=True),
            slug=kwargs['slug'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_credential(self):
        raw_token = (self.request.GET.get('token') or self.request.POST.get('token') or '').strip()
        if not raw_token:
            return None, ''
        try:
            credential = resolve_credential(raw_token)
        except ElectionCredential.DoesNotExist:
            return None, raw_token
        if credential.event_id != self.event.id:
            return None, raw_token
        credential.mark_opened()
        return credential, raw_token

    def post(self, request, *args, **kwargs):
        token_form = CredentialTokenForm(request.POST)
        if 'lookup' in request.POST:
            if token_form.is_valid():
                return redirect(f'{reverse("elections:vote", args=[self.event.slug])}?token={token_form.cleaned_data["token"]}')
            messages.error(request, 'Enter a valid credential code.')
            return redirect('elections:vote', slug=self.event.slug)

        credential, raw_token = self.get_credential()
        if not credential:
            messages.error(request, 'Credential not found.')
            return redirect('elections:vote', slug=self.event.slug)
        try:
            ballot = cast_ballot(self.event, raw_token, request.POST, request=request)
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return redirect(f'{reverse("elections:vote", args=[self.event.slug])}?token={raw_token}')
        return redirect(ballot.receipt.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        credential, raw_token = self.get_credential()
        context['event'] = self.event
        context['credential'] = credential
        context['token'] = raw_token
        context['positions'] = self.event.election_positions.filter(is_active=True).prefetch_related('candidates')
        context['token_form'] = CredentialTokenForm()
        context['accepts_ballots'] = self.event.status == Event.Status.OPEN
        return context


class PublicElectionReceiptView(DetailView):
    model = BallotReceipt
    template_name = 'elections/receipt.html'
    context_object_name = 'receipt'

    def get_object(self, queryset=None):
        return get_object_or_404(
            BallotReceipt.objects.select_related('ballot__event'),
            ballot__event__slug=self.kwargs['slug'],
            ballot__event__kind=Event.Kind.SECURE_ELECTION,
            code=self.kwargs['receipt_code'],
        )


class PublicElectionResultsView(DetailView):
    model = Event
    template_name = 'elections/results.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.filter(kind=Event.Kind.SECURE_ELECTION, is_public=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        public = results_are_public(event)
        context['results_public'] = public
        context['latest_tally'] = event.tally_snapshots.first()
        context['live_totals'] = build_tally(event) if public and not context['latest_tally'] else None
        if public:
            context['receipts'] = event.ballots.exclude(receipt__isnull=True).values_list('receipt__code', flat=True).order_by('receipt__code')
        return context


class PublicElectionVerifyReceiptView(View):
    def get(self, request, *args, **kwargs):
        event = get_object_or_404(
            Event.objects.filter(kind=Event.Kind.SECURE_ELECTION, is_public=True),
            slug=kwargs['slug'],
        )
        if not results_are_public(event):
            return HttpResponse('<div class="mt-4 p-4 rounded-xl border border-amber-200 bg-amber-50 text-amber-800 text-sm">Results are not public yet.</div>', status=403)
        
        code = request.GET.get('code', '').strip().upper()
        if not code:
            return HttpResponse('<p class="text-sm text-vc-dark-400">Enter a code above.</p>')
        
        exists = BallotReceipt.objects.filter(ballot__event=event, code=code).exists()
        if exists:
            return HttpResponse(f'''
                <div class="mt-4 p-4 rounded-xl border border-green-200 bg-green-50 text-green-800 animate-fade-in flex items-center gap-3">
                    <svg class="h-5 w-5 shrink-0 text-green-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                    <div class="text-left">
                        <p class="font-bold text-sm">Ballot Counted & Verified</p>
                        <p class="text-xs text-green-700 mt-0.5">Receipt <span class="font-mono bg-green-100/50 px-1.5 py-0.5 rounded font-semibold text-[11px]">{code}</span> is securely included in the final tallied results.</p>
                    </div>
                </div>
            ''')
        else:
            return HttpResponse(f'''
                <div class="mt-4 p-4 rounded-xl border border-red-200 bg-red-50 text-red-800 animate-fade-in flex items-center gap-3">
                    <svg class="h-5 w-5 shrink-0 text-red-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                    <div class="text-left">
                        <p class="font-bold text-sm">Receipt Code Not Found</p>
                        <p class="text-xs text-red-700 mt-0.5">We could not find receipt <span class="font-mono bg-red-100/50 px-1.5 py-0.5 rounded font-semibold text-[11px]">{code}</span> in the tallied ballots. Please verify the code and try again.</p>
                    </div>
                </div>
            ''')
