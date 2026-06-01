from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import LedgerEntry, LedgerTransaction, WalletAccount, WithdrawalRequest
from .services import (
    get_available_withdrawal_balance,
    get_organizer_account,
    post_withdrawal_ledger_transaction,
)


@admin.register(WalletAccount)
class WalletAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'owner', 'balance')
    search_fields = ('name', 'owner__email', 'code')


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ('account', 'amount', 'kind', 'created_at')


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = ('reference', 'payment_attempt', 'withdrawal_request', 'posted_at', 'is_balanced')
    search_fields = (
        'reference',
        'payment_attempt__gateway_reference',
        'withdrawal_request__payout_reference',
        'withdrawal_request__organizer__email',
    )
    inlines = [LedgerEntryInline]


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
                raise ValidationError(
                    'Completed withdrawals cannot move back to another status.'
                )
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
class WithdrawalRequestAdmin(admin.ModelAdmin):
    form = WithdrawalRequestAdminForm
    list_display = (
        'organizer',
        'amount',
        'currency',
        'status',
        'requested_at',
        'reviewed_at',
        'completed_at',
    )
    list_filter = ('status', 'currency')
    search_fields = (
        'organizer__email',
        'payout_name',
        'bank_name',
        'bank_account_number',
        'payout_reference',
    )
    readonly_fields = ('requested_at', 'reviewed_at', 'completed_at', 'reviewed_by')

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
