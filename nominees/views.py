from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView
from django.views import View

from events.views import build_event_leaderboard
from payments.forms import PaymentInitiationForm
from payments.services import resolve_public_payment_status
from votecentral.mixins import SafeIntegrityMixin
from votecentral.rate_limits import is_rate_limited

from .forms import CompetitionCategoryForm, NominationReviewForm, NominationSubmissionForm, NomineeForm
from .models import CompetitionCategory, NominationSubmission, Nominee


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
                Nominee.objects.select_related('category').filter(event=self.get_event()),
                slug=self.kwargs['slug'],
            )
        return self._nominee

    def get_category(self):
        if not hasattr(self, '_category'):
            self._category = get_object_or_404(
                CompetitionCategory.objects.filter(event=self.get_event()),
                slug=self.kwargs['category_slug'],
            )
        return self._category

    def get_submission(self):
        if not hasattr(self, '_submission'):
            self._submission = get_object_or_404(
                NominationSubmission.objects.select_related('event', 'category', 'approved_nominee').filter(event=self.get_event()),
                pk=self.kwargs['pk'],
            )
        return self._submission


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
        if self.event.show_leaderboard:
            context['leaderboard'] = build_event_leaderboard(self.event)
        else:
            context['leaderboard'] = None
            context['leaderboard_hidden'] = True
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
        context['ussd_short_code'] = getattr(settings, 'USSD_SHORT_CODE', '*920*24#')
        return context


class PublicNominationCreateView(CreateView):
    model = NominationSubmission
    form_class = NominationSubmissionForm
    template_name = 'events/nominate.html'

    def dispatch(self, request, *args, **kwargs):
        from events.models import Event

        self.event = get_object_or_404(
            Event.objects.filter(
                kind=Event.Kind.PAID_COMPETITION,
                is_public=True,
            ),
            slug=self.kwargs['event_slug'],
        )
        if self.event.status in {Event.Status.ARCHIVED, Event.Status.CANCELLED}:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

    def form_valid(self, form):
        if is_rate_limited(self.request, 'public-nomination-submit', 10, 3600):
            form.add_error(None, 'Too many nomination attempts from this network. Please try again later.')
            return self.form_invalid(form)
        if not self.event.accepts_nominations():
            form.add_error(None, 'Nominations are not open for this event right now.')
            return self.form_invalid(form)

        form.instance.event = self.event
        try:
            response = super().form_valid(form)
        except ValidationError as exc:
            if hasattr(exc, 'message_dict'):
                for field, errors in exc.message_dict.items():
                    for error in errors:
                        form.add_error(field if field in form.fields else None, error)
            else:
                form.add_error(None, exc.messages[0] if exc.messages else 'Unable to submit nomination.')
            return self.form_invalid(form)

        from notifications.services import create_in_app_notification, queue_nomination_submitted

        create_in_app_notification(
            user=self.event.owner,
            title='New Nomination Submission',
            message=f"'{self.object.name}' submitted a nomination for '{self.object.category.name}' in '{self.event.title}'.",
            link=reverse('dashboard:nomination_review', args=[self.event.slug, self.object.pk]),
            level='info',
            event=self.event,
        )
        queue_nomination_submitted(self.object)
        messages.success(self.request, 'Your nomination has been submitted for review.')
        return response

    def get_success_url(self):
        return reverse('events:nominate', args=[self.event.slug])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['state'] = self.event.public_state()
        context['nominations_open'] = self.event.accepts_nominations()
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

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
        try:
            nominee.delete()
            messages.success(request, 'Nominee deleted.')
        except ProtectedError:
            messages.error(request, 'This nominee cannot be deleted because they have associated payments or votes.')
        return HttpResponseRedirect(event.get_dashboard_url())


class DashboardCategoryCreateView(SafeIntegrityMixin, NomineeEventMixin, CreateView):
    model = CompetitionCategory
    form_class = CompetitionCategoryForm
    template_name = 'dashboard/nominees/category_form.html'
    success_message = 'Category added.'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.event = self.event
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

    def get_success_url(self):
        return self.event.get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        return context


class DashboardCategoryUpdateView(SafeIntegrityMixin, NomineeEventMixin, UpdateView):
    model = CompetitionCategory
    form_class = CompetitionCategoryForm
    template_name = 'dashboard/nominees/category_form.html'
    success_message = 'Category updated.'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.get_category()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

    def get_success_url(self):
        return self.event.get_dashboard_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        return context


class DashboardCategoryDeleteView(NomineeEventMixin, View):
    def post(self, request, *args, **kwargs):
        category = self.get_category()
        event = category.event
        try:
            category.delete()
        except ProtectedError:
            messages.error(request, 'This category cannot be deleted because it still has nominees or nomination submissions.')
        else:
            messages.success(request, 'Category deleted.')
        return HttpResponseRedirect(event.get_dashboard_url())


class DashboardNominationSubmissionListView(NomineeEventMixin, ListView):
    model = NominationSubmission
    template_name = 'dashboard/nominees/submissions.html'
    context_object_name = 'submissions'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        self.selected_status = (request.GET.get('status') or NominationSubmission.Status.PENDING).strip().lower()
        if self.selected_status not in {choice for choice, _ in NominationSubmission.Status.choices}:
            self.selected_status = NominationSubmission.Status.PENDING
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.event.nomination_submissions.select_related('category', 'approved_nominee')
        return queryset.filter(status=self.selected_status)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['selected_status'] = self.selected_status
        context['status_choices'] = NominationSubmission.Status.choices
        status_counts = {
            status: self.event.nomination_submissions.filter(status=status).count()
            for status, _label in NominationSubmission.Status.choices
        }
        context['status_counts'] = status_counts
        context['status_tabs'] = [
            {'value': status, 'label': label, 'count': status_counts[status]}
            for status, label in NominationSubmission.Status.choices
        ]
        return context


class DashboardNominationReviewView(SafeIntegrityMixin, NomineeEventMixin, UpdateView):
    model = NominationSubmission
    form_class = NominationReviewForm
    template_name = 'dashboard/nominees/submission_review.html'

    def dispatch(self, request, *args, **kwargs):
        self.event = self.get_event()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.get_submission()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event'] = self.event
        return kwargs

    def form_valid(self, form):
        action = (self.request.POST.get('action') or 'approve').strip().lower()
        submission = form.instance

        if action not in {'approve', 'reject'}:
            form.add_error(None, 'Unknown review action.')
            return self.form_invalid(form)

        with transaction.atomic():
            submission = form.save(commit=False)
            if action == 'approve':
                nominee = submission.approved_nominee or Nominee(event=self.event)
                nominee.category = form.cleaned_data['category']
                nominee.name = form.cleaned_data['name']
                nominee.bio = form.cleaned_data['bio']
                photo_file = form.cleaned_data['photo']
                if photo_file:
                    from django.db.models.fields.files import FieldFile
                    from django.core.files.base import ContentFile
                    import os

                    if isinstance(photo_file, FieldFile) and photo_file.name.startswith('nomination-submissions/'):
                        if not nominee.photo or nominee.photo.name.startswith('nomination-submissions/'):
                            try:
                                photo_file.open('rb')
                                file_content = photo_file.read()
                                filename = os.path.basename(photo_file.name)
                                nominee.photo.save(filename, ContentFile(file_content), save=False)
                            finally:
                                photo_file.close()
                    else:
                        nominee.photo = photo_file
                else:
                    nominee.photo = None
                nominee.email = form.cleaned_data['email']
                nominee.phone_number = form.cleaned_data['phone_number']
                nominee.display_order = form.cleaned_data['display_order']
                nominee.is_active = form.cleaned_data['is_active']
                nominee.event = self.event
                nominee.save()

                submission.status = NominationSubmission.Status.APPROVED
                submission.approved_nominee = nominee
                submission.save()

                from notifications.services import queue_nomination_approved

                queue_nomination_approved(submission)
                messages.success(self.request, 'Nomination approved and nominee created.')
            else:
                if submission.approved_nominee_id:
                    form.add_error(None, 'Approved submissions cannot be rejected here. Edit the nominee directly instead.')
                    return self.form_invalid(form)
                submission.status = NominationSubmission.Status.REJECTED
                submission.approved_nominee = None
                submission.save()

                from notifications.services import queue_nomination_rejected

                queue_nomination_rejected(submission)
                messages.success(self.request, 'Nomination rejected.')

        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('dashboard:nomination_review', args=[self.event.slug, self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        context['submission'] = self.object
        return context
