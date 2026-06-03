from django.contrib import admin
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter

from elections.services import (
    certify_election,
    close_election,
    generate_tally,
    lock_election_roster,
    open_election,
    publish_election_results,
)
from votecentral.admin_utils import ExportCsvMixin, run_guarded_action, status_badge
from .models import ContactInquiry, Event


@admin.register(Event)
class EventAdmin(ExportCsvMixin, ModelAdmin):
    list_display = (
        'title',
        'owner',
        'kind',
        'status_display',
        'commission_percent_display',
        'commission_status_display',
        'vote_price',
        'currency',
        'is_public',
        'start_at',
        'end_at',
        'created_at',
    )
    list_filter_submit = True
    list_filter = (
        ('kind', ChoicesDropdownFilter),
        ('status', ChoicesDropdownFilter),
        ('start_at', RangeDateTimeFilter),
        ('end_at', RangeDateTimeFilter),
        'currency',
        'is_public',
        'show_leaderboard',
    )
    search_fields = ('title', 'owner__email', 'slug')
    list_select_related = ('owner',)
    autocomplete_fields = ('owner',)
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('platform_commission_set_at', 'platform_commission_set_by')
    actions = ExportCsvMixin.actions + (
        'publish_competitions',
        'unpublish_competitions',
        'close_competitions',
        'lock_secure_rosters',
        'open_secure_elections',
        'close_secure_elections',
        'tally_secure_elections',
        'publish_secure_results',
        'certify_secure_elections',
    )

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    @admin.display(description='Commission', ordering='platform_commission_percent')
    def commission_percent_display(self, obj):
        if obj.kind != Event.Kind.PAID_COMPETITION or obj.platform_commission_percent is None:
            return 'Unset'
        return f'{obj.platform_commission_percent}%'

    @admin.display(description='Commission Status')
    def commission_status_display(self, obj):
        if obj.kind != Event.Kind.PAID_COMPETITION:
            return status_badge('neutral', 'N/A')
        if obj.commission_is_locked():
            return status_badge('success', 'Locked')
        if obj.has_platform_commission():
            return status_badge('info', 'Configured')
        return status_badge('warning', 'Unset')

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.commission_is_locked():
            readonly_fields.append('platform_commission_percent')
        return readonly_fields

    def save_model(self, request, obj, form, change):
        if obj.kind == Event.Kind.PAID_COMPETITION:
            previous = Event.objects.filter(pk=obj.pk).first() if change else None
            previous_percent = previous.platform_commission_percent if previous else None
            if obj.platform_commission_percent != previous_percent:
                if obj.platform_commission_percent is None:
                    obj.platform_commission_set_at = None
                    obj.platform_commission_set_by = None
                else:
                    obj.platform_commission_set_at = timezone.now()
                    obj.platform_commission_set_by = request.user
        else:
            obj.platform_commission_percent = None
            obj.platform_commission_set_at = None
            obj.platform_commission_set_by = None
        super().save_model(request, obj, form, change)

    @admin.action(description='Publish selected paid competitions')
    def publish_competitions(self, request, queryset):
        def publish(event):
            if event.kind != Event.Kind.PAID_COMPETITION:
                raise ValueError(f'{event.title} is not a paid competition.')
            event.publish()

        run_guarded_action(self, request, queryset, publish, 'paid competitions published')

    @admin.action(description='Move selected paid competitions to draft')
    def unpublish_competitions(self, request, queryset):
        def unpublish(event):
            if event.kind != Event.Kind.PAID_COMPETITION:
                raise ValueError(f'{event.title} is not a paid competition.')
            event.unpublish()

        run_guarded_action(self, request, queryset, unpublish, 'paid competitions moved to draft')

    @admin.action(description='Close selected paid competitions')
    def close_competitions(self, request, queryset):
        def close(event):
            if event.kind != Event.Kind.PAID_COMPETITION:
                raise ValueError(f'{event.title} is not a paid competition.')
            event.close()

        run_guarded_action(self, request, queryset, close, 'paid competitions closed')

    @admin.action(description='Lock rosters for selected secure elections')
    def lock_secure_rosters(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: lock_election_roster(event, actor=request.user, request=request),
            'secure election rosters locked',
        )

    @admin.action(description='Open selected secure elections')
    def open_secure_elections(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: open_election(event, actor=request.user, request=request),
            'secure elections opened',
        )

    @admin.action(description='Close selected secure elections')
    def close_secure_elections(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: close_election(event, actor=request.user, request=request),
            'secure elections closed',
        )

    @admin.action(description='Tally selected secure elections')
    def tally_secure_elections(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: generate_tally(event, actor=request.user, request=request),
            'secure elections tallied',
        )

    @admin.action(description='Publish results for selected secure elections')
    def publish_secure_results(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: publish_election_results(event, actor=request.user, request=request),
            'secure election results published',
        )

    @admin.action(description='Certify selected secure elections')
    def certify_secure_elections(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset.filter(kind=Event.Kind.SECURE_ELECTION),
            lambda event: certify_election(event, actor=request.user, request=request),
            'secure elections certified',
        )


@admin.register(ContactInquiry)
class ContactInquiryAdmin(ModelAdmin):
    list_display = (
        'created_at',
        'name',
        'email',
        'phone_number',
        'heard_about_us',
    )
    list_filter_submit = True
    list_filter = (
        ('heard_about_us', ChoicesDropdownFilter),
        ('created_at', RangeDateTimeFilter),
    )
    search_fields = ('name', 'email', 'phone_number', 'message')
    date_hierarchy = 'created_at'
