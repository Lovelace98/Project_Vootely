from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)

from votecentral.admin_utils import ExportCsvMixin, ReadOnlyAdminMixin, run_guarded_action, status_badge
from .models import LedgerEntry, LedgerTransaction, WalletAccount, WithdrawalRequest
from .services import (
    get_available_withdrawal_balance,
    get_organizer_account,
    post_withdrawal_ledger_transaction,
)


@admin.register(WalletAccount)
class WalletAccountAdmin(ExportCsvMixin, ModelAdmin):
    list_display = ('name', 'kind', 'owner', 'code', 'balance', 'created_at')
    list_filter = (('kind', ChoicesDropdownFilter), ('created_at', RangeDateTimeFilter))
    search_fields = ('name', 'owner__email', 'code')
    list_select_related = ('owner',)
    autocomplete_fields = ('owner',)
    date_hierarchy = 'created_at'
    readonly_fields = ('balance', 'created_at')
    actions = ExportCsvMixin.actions


class LedgerEntryInline(TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False
    readonly_fields = ('account', 'amount', 'kind', 'created_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('reference', 'payment_attempt', 'withdrawal_request', 'posted_at', 'balanced_display')
    list_filter = (('posted_at', RangeDateTimeFilter),)
    search_fields = (
        'reference',
        'description',
        'payment_attempt__gateway_reference',
        'withdrawal_request__payout_reference',
        'withdrawal_request__organizer__email',
    )
    list_select_related = ('payment_attempt', 'withdrawal_request')
    autocomplete_fields = ('payment_attempt', 'withdrawal_request')
    date_hierarchy = 'posted_at'
    readonly_fields = (
        'reference',
        'payment_attempt',
        'withdrawal_request',
        'description',
        'metadata',
        'posted_at',
        'is_balanced',
    )
    inlines = [LedgerEntryInline]
    actions = ExportCsvMixin.actions

    @admin.display(description='Balanced')
    def balanced_display(self, obj):
        return status_badge('active' if obj.is_balanced else 'danger', 'Balanced' if obj.is_balanced else 'Imbalanced')


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ExportCsvMixin, ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('transaction', 'account', 'kind', 'amount', 'created_at')
    list_filter_submit = True
    list_filter = (
        ('kind', ChoicesDropdownFilter),
        ('account', AutocompleteSelectFilter),
        ('amount', RangeNumericFilter),
        ('created_at', RangeDateTimeFilter),
    )
    search_fields = ('transaction__reference', 'account__name', 'account__owner__email', 'kind')
    list_select_related = ('transaction', 'account', 'account__owner')
    autocomplete_fields = ('transaction', 'account')
    date_hierarchy = 'created_at'
    readonly_fields = ('transaction', 'account', 'amount', 'kind', 'created_at')
    actions = ExportCsvMixin.actions


class WithdrawalRequestAdminForm(forms.ModelForm):
    class Meta:
        model = WithdrawalRequest
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        organizer = cleaned_data.get('organizer') or self.instance.organizer
        status = cleaned_data.get('status') or self.instance.status
        amount = cleaned_data.get('amount') or self.instance.amount
        if not organizer or amount is None:
            return cleaned_data
        if self.instance.pk and self.instance.status == WithdrawalRequest.Status.COMPLETED:
            if status != WithdrawalRequest.Status.COMPLETED:
                raise ValidationError('Completed withdrawals cannot move back to another status.')
        if status in {
            WithdrawalRequest.Status.APPROVED,
            WithdrawalRequest.Status.PROCESSING,
            WithdrawalRequest.Status.COMPLETED,
        } and amount > get_available_withdrawal_balance(
            organizer,
            exclude_withdrawal=self.instance,
        ):
            raise ValidationError('This withdrawal exceeds the organizer available balance.')
        return cleaned_data


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(ExportCsvMixin, ModelAdmin):
    form = WithdrawalRequestAdminForm
    list_display = (
        'organizer',
        'amount',
        'currency',
        'payout_type',
        'payout_name',
        'status_display',
        'requested_at',
        'reviewed_at',
        'completed_at',
    )
    list_filter_submit = True
    list_filter = (
        ('status', ChoicesDropdownFilter),
        ('payout_type', ChoicesDropdownFilter),
        ('amount', RangeNumericFilter),
        ('requested_at', RangeDateTimeFilter),
        'currency',
    )
    search_fields = (
        'organizer__email',
        'payout_name',
        'bank_name',
        'bank_code',
        'bank_account_number',
        'payout_reference',
        'review_notes',
    )
    list_select_related = ('organizer', 'wallet_account', 'reviewed_by')
    autocomplete_fields = ('organizer', 'wallet_account', 'reviewed_by')
    date_hierarchy = 'requested_at'
    readonly_fields = ('requested_at', 'reviewed_at', 'completed_at', 'reviewed_by')
    actions = ExportCsvMixin.actions + (
        'approve_withdrawals',
        'mark_withdrawals_processing',
        'complete_withdrawals',
        'reject_withdrawals',
    )

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    def _prepare_transition(self, request, obj, status):
        previous_status = obj.status
        if not obj.wallet_account_id and obj.organizer_id:
            obj.wallet_account = get_organizer_account(obj.organizer)
        if status in {
            WithdrawalRequest.Status.APPROVED,
            WithdrawalRequest.Status.PROCESSING,
            WithdrawalRequest.Status.COMPLETED,
        } and obj.amount > get_available_withdrawal_balance(
            obj.organizer,
            exclude_withdrawal=obj,
        ):
            raise ValidationError('This withdrawal exceeds the organizer available balance.')

        obj.status = status
        if obj.status != WithdrawalRequest.Status.PENDING:
            obj.reviewed_at = obj.reviewed_at or timezone.now()
            obj.reviewed_by = obj.reviewed_by or request.user
        if obj.status == WithdrawalRequest.Status.COMPLETED:
            obj.completed_at = obj.completed_at or timezone.now()

        obj.full_clean()
        obj.save()
        if obj.status == WithdrawalRequest.Status.COMPLETED:
            post_withdrawal_ledger_transaction(obj)
        if previous_status != obj.status:
            from notifications.services import queue_withdrawal_status_notification

            queue_withdrawal_status_notification(obj, obj.status)

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = WithdrawalRequest.objects.get(pk=obj.pk).status
        if not obj.wallet_account_id and obj.organizer_id:
            obj.wallet_account = get_organizer_account(obj.organizer)
        if obj.status != WithdrawalRequest.Status.PENDING:
            if not obj.reviewed_at:
                obj.reviewed_at = timezone.now()
            if not obj.reviewed_by_id:
                obj.reviewed_by = request.user
        if obj.status == WithdrawalRequest.Status.COMPLETED and not obj.completed_at:
            obj.completed_at = timezone.now()

        super().save_model(request, obj, form, change)

        if obj.status == WithdrawalRequest.Status.COMPLETED:
            post_withdrawal_ledger_transaction(obj)
        if previous_status != obj.status:
            from notifications.services import queue_withdrawal_status_notification

            queue_withdrawal_status_notification(obj, obj.status)

    @admin.action(description='Approve selected withdrawals')
    def approve_withdrawals(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset,
            lambda withdrawal: self._prepare_transition(request, withdrawal, WithdrawalRequest.Status.APPROVED),
            'withdrawals approved',
        )

    @admin.action(description='Mark selected withdrawals as processing')
    def mark_withdrawals_processing(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset,
            lambda withdrawal: self._prepare_transition(request, withdrawal, WithdrawalRequest.Status.PROCESSING),
            'withdrawals marked processing',
        )

    @admin.action(description='Complete selected withdrawals')
    def complete_withdrawals(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset,
            lambda withdrawal: self._prepare_transition(request, withdrawal, WithdrawalRequest.Status.COMPLETED),
            'withdrawals completed',
        )

    @admin.action(description='Reject selected withdrawals')
    def reject_withdrawals(self, request, queryset):
        run_guarded_action(
            self,
            request,
            queryset,
            lambda withdrawal: self._prepare_transition(request, withdrawal, WithdrawalRequest.Status.REJECTED),
            'withdrawals rejected',
        )
