from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)

from votecentral.admin_utils import ExportCsvMixin, ReadOnlyAdminMixin, status_badge
from .models import (
    Ballot,
    BallotReceipt,
    BallotSelection,
    ElectionAuditLog,
    ElectionCandidate,
    ElectionConfig,
    ElectionCredential,
    ElectionCredentialExport,
    ElectionInvoice,
    ElectionPosition,
    ElectionPricingPlan,
    ElectionPricingTier,
    ElectionTallySnapshot,
    ElectionVoter,
    OrganizerPaymentAttempt,
)


class ElectionPricingTierInline(TabularInline):
    model = ElectionPricingTier
    extra = 0


@admin.register(ElectionPricingPlan)
class ElectionPricingPlanAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'currency', 'minimum_fee', 'active_display', 'created_at', 'updated_at')
    list_filter = ('is_active', 'currency')
    search_fields = ('name', 'currency')
    date_hierarchy = 'created_at'
    inlines = [ElectionPricingTierInline]
    actions = ExportCsvMixin.actions

    @admin.display(description='Active', ordering='is_active')
    def active_display(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


@admin.register(ElectionPricingTier)
class ElectionPricingTierAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('plan', 'start_count', 'end_count', 'rate')
    list_filter = (('plan', AutocompleteSelectFilter), ('rate', RangeNumericFilter))
    search_fields = ('plan__name',)
    list_select_related = ('plan',)
    autocomplete_fields = ('plan',)
    actions = ExportCsvMixin.actions


@admin.register(ElectionConfig)
class ElectionConfigAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('event', 'results_visibility', 'allow_abstain', 'receipt_verification_enabled', 'updated_at')
    list_filter = (('results_visibility', ChoicesDropdownFilter), 'allow_abstain', 'receipt_verification_enabled')
    search_fields = ('event__title', 'event__slug', 'event__owner__email')
    list_select_related = ('event',)
    autocomplete_fields = ('event',)
    date_hierarchy = 'created_at'
    actions = ExportCsvMixin.actions


@admin.register(ElectionPosition)
class ElectionPositionAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('title', 'event', 'max_choices', 'display_order', 'active_display', 'created_at')
    list_filter = ('is_active', ('event', AutocompleteSelectFilter))
    search_fields = ('title', 'slug', 'event__title', 'event__slug', 'event__owner__email')
    list_select_related = ('event',)
    autocomplete_fields = ('event',)
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    actions = ExportCsvMixin.actions

    @admin.display(description='Active', ordering='is_active')
    def active_display(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


@admin.register(ElectionCandidate)
class ElectionCandidateAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'position', 'event', 'email', 'phone', 'display_order', 'active_display')
    list_filter = ('is_active', ('event', AutocompleteSelectFilter), ('position', AutocompleteSelectFilter))
    search_fields = ('name', 'slug', 'email', 'phone', 'position__title', 'event__title', 'event__slug')
    list_select_related = ('event', 'position')
    autocomplete_fields = ('event', 'position')
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('name',)}
    actions = ExportCsvMixin.actions

    @admin.display(description='Active', ordering='is_active')
    def active_display(self, obj):
        return status_badge('active' if obj.is_active else 'revoked', 'Active' if obj.is_active else 'Inactive')


@admin.register(ElectionVoter)
class ElectionVoterAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('external_id', 'name', 'event', 'email', 'phone', 'status_display', 'created_at')
    list_filter = (('status', ChoicesDropdownFilter), ('event', AutocompleteSelectFilter))
    search_fields = ('external_id', 'name', 'email', 'phone', 'event__title', 'event__slug')
    list_select_related = ('event',)
    autocomplete_fields = ('event',)
    date_hierarchy = 'created_at'
    readonly_fields = ('row_hash', 'metadata', 'created_at', 'updated_at')
    actions = ExportCsvMixin.actions

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())


@admin.register(ElectionCredential)
class ElectionCredentialAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('voter', 'event', 'status_display', 'issued_at', 'opened_at', 'used_at', 'revoked_at')
    list_filter = (
        ('status', ChoicesDropdownFilter),
        ('event', AutocompleteSelectFilter),
        ('issued_at', RangeDateTimeFilter),
        ('used_at', RangeDateTimeFilter),
    )
    search_fields = ('voter__external_id', 'voter__name', 'voter__email', 'event__title', 'event__slug', 'token_hash')
    list_select_related = ('event', 'voter', 'reissued_from')
    autocomplete_fields = ('event', 'voter', 'reissued_from')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'token_hash',
        'issued_at',
        'opened_at',
        'used_at',
        'revoked_at',
        'metadata',
        'created_at',
    )
    actions = ExportCsvMixin.actions

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())


@admin.register(ElectionCredentialExport)
class ElectionCredentialExportAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('event', 'row_count', 'generated_by', 'created_at')
    list_filter = (('event', AutocompleteSelectFilter), ('created_at', RangeDateTimeFilter))
    search_fields = ('event__title', 'event__slug', 'generated_by__email')
    list_select_related = ('event', 'generated_by')
    autocomplete_fields = ('event', 'generated_by')
    date_hierarchy = 'created_at'
    readonly_fields = ('event', 'generated_by', 'row_count', 'rows', 'created_at')
    actions = ExportCsvMixin.actions


@admin.register(ElectionInvoice)
class ElectionInvoiceAdmin(ExportCsvMixin, ModelAdmin):
    list_display = (
        'event',
        'status_display',
        'amount',
        'amount_paid',
        'currency',
        'voter_count',
        'covered_voter_count',
        'is_top_up',
        'paid_at',
    )
    list_filter_submit = True
    list_filter = (
        ('status', ChoicesDropdownFilter),
        ('event', AutocompleteSelectFilter),
        ('amount', RangeNumericFilter),
        ('created_at', RangeDateTimeFilter),
        'currency',
        'is_top_up',
    )
    search_fields = ('event__title', 'event__slug', 'pricing_plan__name')
    list_select_related = ('event', 'pricing_plan')
    autocomplete_fields = ('event', 'pricing_plan')
    date_hierarchy = 'created_at'
    readonly_fields = ('price_snapshot', 'paid_at', 'created_at', 'updated_at')
    actions = ExportCsvMixin.actions

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())


@admin.register(OrganizerPaymentAttempt)
class OrganizerPaymentAttemptAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('gateway_reference', 'event', 'invoice', 'owner', 'status_display', 'amount', 'currency', 'initiated_at')
    list_filter_submit = True
    list_filter = (
        ('status', ChoicesDropdownFilter),
        ('gateway', ChoicesDropdownFilter),
        ('event', AutocompleteSelectFilter),
        ('amount', RangeNumericFilter),
        ('initiated_at', RangeDateTimeFilter),
        'currency',
    )
    search_fields = ('gateway_reference', 'event__title', 'event__slug', 'owner__email', 'payer_email', 'failure_reason')
    list_select_related = ('event', 'invoice', 'owner')
    autocomplete_fields = ('event', 'invoice', 'owner')
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


class BallotSelectionInline(TabularInline):
    model = BallotSelection
    extra = 0
    can_delete = False
    readonly_fields = ('position', 'candidate')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ballot)
class BallotAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('anonymous_id', 'event', 'cast_at', 'ip_address')
    list_filter = (('event', AutocompleteSelectFilter), ('cast_at', RangeDateTimeFilter))
    search_fields = ('anonymous_id', 'receipt_hash', 'event__title', 'event__slug', 'ip_address')
    list_select_related = ('event',)
    autocomplete_fields = ('event',)
    date_hierarchy = 'cast_at'
    readonly_fields = ('event', 'anonymous_id', 'receipt_hash', 'cast_at', 'ip_address', 'user_agent', 'metadata')
    inlines = [BallotSelectionInline]
    actions = ExportCsvMixin.actions


@admin.register(BallotSelection)
class BallotSelectionAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('ballot', 'position', 'candidate')
    list_filter = (('position', AutocompleteSelectFilter), ('candidate', AutocompleteSelectFilter))
    search_fields = ('ballot__anonymous_id', 'position__title', 'candidate__name')
    list_select_related = ('ballot', 'position', 'candidate')
    autocomplete_fields = ('ballot', 'position', 'candidate')
    readonly_fields = ('ballot', 'position', 'candidate')
    actions = ExportCsvMixin.actions


@admin.register(BallotReceipt)
class BallotReceiptAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('code', 'ballot', 'created_at')
    list_filter = (('created_at', RangeDateTimeFilter),)
    search_fields = ('code', 'code_hash', 'ballot__anonymous_id', 'ballot__event__title')
    list_select_related = ('ballot',)
    autocomplete_fields = ('ballot',)
    date_hierarchy = 'created_at'
    readonly_fields = ('ballot', 'code', 'code_hash', 'created_at')
    actions = ExportCsvMixin.actions


@admin.register(ElectionAuditLog)
class ElectionAuditLogAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('event', 'action', 'actor', 'object_type', 'object_id', 'created_at')
    list_filter = (('event', AutocompleteSelectFilter), ('created_at', RangeDateTimeFilter), 'action', 'object_type')
    search_fields = ('event__title', 'event__slug', 'action', 'actor__email', 'object_type', 'object_id', 'ip_address')
    list_select_related = ('event', 'actor')
    autocomplete_fields = ('event', 'actor')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'event',
        'actor',
        'action',
        'object_type',
        'object_id',
        'metadata',
        'ip_address',
        'user_agent',
        'created_at',
    )
    actions = ExportCsvMixin.actions


@admin.register(ElectionTallySnapshot)
class ElectionTallySnapshotAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('event', 'ballot_count', 'generated_by', 'generated_at', 'published_at')
    list_filter = (('event', AutocompleteSelectFilter), ('generated_at', RangeDateTimeFilter), ('published_at', RangeDateTimeFilter))
    search_fields = ('event__title', 'event__slug', 'generated_by__email')
    list_select_related = ('event', 'generated_by')
    autocomplete_fields = ('event', 'generated_by')
    date_hierarchy = 'generated_at'
    readonly_fields = ('event', 'totals', 'ballot_count', 'generated_by', 'generated_at', 'published_at')
    actions = ExportCsvMixin.actions
