from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import AutocompleteSelectFilter, RangeDateTimeFilter, RangeNumericFilter

from votecentral.admin_utils import ExportCsvMixin
from .models import VotePurchase, USSDSession


@admin.register(VotePurchase)
class VotePurchaseAdmin(ExportCsvMixin, ModelAdmin):
    list_display = (
        'payment_reference',
        'event',
        'nominee',
        'quantity',
        'amount_paid',
        'currency',
        'voter_email',
        'paid_at',
    )
    list_filter_submit = True
    list_filter = (
        ('event', AutocompleteSelectFilter),
        ('nominee', AutocompleteSelectFilter),
        ('quantity', RangeNumericFilter),
        ('amount_paid', RangeNumericFilter),
        ('paid_at', RangeDateTimeFilter),
        'currency',
    )
    search_fields = (
        'payment_reference',
        'voter_name',
        'voter_email',
        'voter_phone',
        'nominee__name',
        'event__title',
        'event__slug',
    )
    list_select_related = ('event', 'nominee', 'payment_attempt')
    autocomplete_fields = ('event', 'nominee', 'payment_attempt')
    date_hierarchy = 'paid_at'
    readonly_fields = (
        'payment_attempt',
        'payment_reference',
        'ip_address',
        'user_agent',
        'metadata',
        'paid_at',
        'created_at',
    )
    actions = ExportCsvMixin.actions


@admin.register(USSDSession)
class USSDSessionAdmin(ModelAdmin):
    list_display = (
        'session_id',
        'phone_number',
        'current_state',
        'votes_count',
        'ticket_quantity',
        'purchase_for',
        'amount_due',
        'updated_at',
    )
    search_fields = (
        'session_id',
        'phone_number',
        'current_state',
        'recipient_phone',
    )
    list_filter = (
        'current_state',
    )
    readonly_fields = (
        'session_id',
        'phone_number',
        'user_id',
        'current_state',
        'nominee_id',
        'event_id',
        'ticket_type_id',
        'votes_count',
        'ticket_quantity',
        'purchase_for',
        'recipient_phone',
        'amount_due',
        'created_at',
        'updated_at',
    )
