from django.contrib import admin

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


class ElectionPricingTierInline(admin.TabularInline):
    model = ElectionPricingTier
    extra = 0


@admin.register(ElectionPricingPlan)
class ElectionPricingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'currency', 'minimum_fee', 'is_active')
    inlines = [ElectionPricingTierInline]


@admin.register(ElectionConfig)
class ElectionConfigAdmin(admin.ModelAdmin):
    list_display = ('event', 'results_visibility', 'allow_abstain')
    search_fields = ('event__title',)


@admin.register(ElectionPosition)
class ElectionPositionAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'display_order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('title', 'event__title')


@admin.register(ElectionCandidate)
class ElectionCandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'position', 'event', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'position__title', 'event__title')


@admin.register(ElectionVoter)
class ElectionVoterAdmin(admin.ModelAdmin):
    list_display = ('external_id', 'name', 'event', 'email', 'phone', 'status')
    list_filter = ('status',)
    search_fields = ('external_id', 'name', 'email', 'event__title')


@admin.register(ElectionCredential)
class ElectionCredentialAdmin(admin.ModelAdmin):
    list_display = ('voter', 'event', 'status', 'issued_at', 'used_at')
    list_filter = ('status',)
    search_fields = ('voter__external_id', 'voter__name', 'event__title')
    readonly_fields = ('token_hash',)


@admin.register(ElectionCredentialExport)
class ElectionCredentialExportAdmin(admin.ModelAdmin):
    list_display = ('event', 'row_count', 'generated_by', 'created_at')
    search_fields = ('event__title',)


@admin.register(ElectionInvoice)
class ElectionInvoiceAdmin(admin.ModelAdmin):
    list_display = ('event', 'status', 'amount', 'currency', 'voter_count', 'is_top_up', 'paid_at')
    list_filter = ('status', 'currency', 'is_top_up')
    search_fields = ('event__title',)


@admin.register(OrganizerPaymentAttempt)
class OrganizerPaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ('gateway_reference', 'event', 'invoice', 'status', 'amount', 'currency')
    list_filter = ('status', 'gateway')
    search_fields = ('gateway_reference', 'event__title', 'owner__email')


class BallotSelectionInline(admin.TabularInline):
    model = BallotSelection
    extra = 0


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ('anonymous_id', 'event', 'cast_at')
    search_fields = ('anonymous_id', 'event__title')
    inlines = [BallotSelectionInline]


@admin.register(BallotReceipt)
class BallotReceiptAdmin(admin.ModelAdmin):
    list_display = ('code', 'ballot', 'created_at')
    readonly_fields = ('code_hash',)
    search_fields = ('code', 'ballot__anonymous_id')


@admin.register(ElectionAuditLog)
class ElectionAuditLogAdmin(admin.ModelAdmin):
    list_display = ('event', 'action', 'actor', 'created_at')
    list_filter = ('action',)
    search_fields = ('event__title', 'action', 'actor__email')
    readonly_fields = ('metadata',)


@admin.register(ElectionTallySnapshot)
class ElectionTallySnapshotAdmin(admin.ModelAdmin):
    list_display = ('event', 'ballot_count', 'generated_by', 'generated_at', 'published_at')
    search_fields = ('event__title',)
