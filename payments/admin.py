from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)

from votecentral.admin_utils import ExportCsvMixin, status_badge
from .models import PaymentAttempt


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ExportCsvMixin, ModelAdmin):
    list_display = (
        'gateway_reference',
        'event',
        'nominee',
        'status_display',
        'amount',
        'currency',
        'vote_quantity',
        'initiated_at',
        'completed_at',
    )
    list_filter_submit = True
    list_filter = (
        ('status', ChoicesDropdownFilter),
        ('gateway', ChoicesDropdownFilter),
        ('amount', RangeNumericFilter),
        ('initiated_at', RangeDateTimeFilter),
        ('event', AutocompleteSelectFilter),
        'currency',
    )
    search_fields = (
        'gateway_reference',
        'gateway_status',
        'failure_reason',
        'voter_name',
        'voter_email',
        'voter_phone',
        'event__title',
        'event__slug',
        'nominee__name',
    )
    list_select_related = ('event', 'nominee')
    autocomplete_fields = ('event', 'nominee')
    date_hierarchy = 'initiated_at'
    readonly_fields = (
        'gateway_reference',
        'gateway_access_code',
        'gateway_checkout_url',
        'gateway_status',
        'failure_reason',
        'gateway_response',
        'webhook_payload',
        'metadata',
        'initiated_at',
        'callback_received_at',
        'completed_at',
        'confirmed_webhook_at',
    )
    actions = ExportCsvMixin.actions

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())
