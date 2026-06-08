from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, QuerySet, Sum, Value
from django.db.models.functions import Coalesce


class WalletAccountQuerySet(QuerySet):
    def annotate_balance(self):
        return self.annotate(
            _balance=Coalesce(
                Sum('entries__amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )


class WalletAccount(models.Model):
    class Kind(models.TextChoices):
        ORGANIZER = 'organizer', 'Organizer'
        PLATFORM = 'platform', 'Platform'

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='wallet_account',
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    code = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = WalletAccountQuerySet.as_manager()

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    @property
    def balance(self):
        if hasattr(self, '_balance'):
            return self._balance
        return self.entries.aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']


class LedgerTransaction(models.Model):
    reference = models.CharField(max_length=100, unique=True)
    payment_attempt = models.OneToOneField(
        'payments.PaymentAttempt',
        on_delete=models.PROTECT,
        related_name='ledger_transaction',
        null=True,
        blank=True,
    )
    ticket_purchase = models.OneToOneField(
        'ticketing.TicketPurchase',
        on_delete=models.PROTECT,
        related_name='ledger_transaction',
        null=True,
        blank=True,
    )
    withdrawal_request = models.OneToOneField(
        'wallets.WithdrawalRequest',
        on_delete=models.PROTECT,
        related_name='ledger_transaction',
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-posted_at',)

    def __str__(self):
        return self.reference

    @property
    def is_balanced(self):
        total = self.entries.aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        return total == Decimal('0.00')

    def ensure_balanced(self):
        total = self.entries.aggregate(
            total=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )['total']
        if abs(total) > Decimal('0.001'):
            raise ValidationError(
                f'Ledger transaction {self.reference} is unbalanced: entries sum to {total}',
            )


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        ORGANIZER_SALE_CREDIT = 'organizer_sale_credit', 'Organizer Sale Credit'
        PLATFORM_FEE_CREDIT = 'platform_fee_credit', 'Platform Fee Credit'
        GATEWAY_SETTLEMENT_DEBIT = 'gateway_settlement_debit', 'Gateway Settlement Debit'
        ORGANIZER_WITHDRAWAL_DEBIT = 'organizer_withdrawal_debit', 'Organizer Withdrawal Debit'
        PLATFORM_PAYOUT_CREDIT = 'platform_payout_credit', 'Platform Payout Credit'
        ADJUSTMENT = 'adjustment', 'Adjustment'

    transaction = models.ForeignKey(
        LedgerTransaction,
        on_delete=models.PROTECT,
        related_name='entries',
    )
    account = models.ForeignKey(
        WalletAccount,
        on_delete=models.PROTECT,
        related_name='entries',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    kind = models.CharField(max_length=40, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('pk',)
        indexes = [
            models.Index(fields=('account', 'kind', '-created_at'), name='ledger_acct_kind_created'),
            models.Index(fields=('transaction', 'kind'), name='ledger_tx_kind'),
        ]

    def __str__(self):
        return f'{self.kind} {self.amount}'


class WithdrawalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        REJECTED = 'rejected', 'Rejected'
        FAILED = 'failed', 'Failed'

    class PayoutType(models.TextChoices):
        BANK = 'bank', 'Bank Account'
        MOBILE_MONEY = 'mobile_money', 'Mobile Money'

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='withdrawal_requests',
    )
    wallet_account = models.ForeignKey(
        WalletAccount,
        on_delete=models.PROTECT,
        related_name='withdrawal_requests',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    payout_type = models.CharField(
        max_length=16,
        choices=PayoutType.choices,
        default=PayoutType.BANK,
        blank=True,
    )
    payout_name = models.CharField(max_length=120)
    bank_name = models.CharField(max_length=120)
    bank_code = models.CharField(max_length=16, blank=True, default='')
    bank_account_number = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    review_notes = models.TextField(blank=True)
    payout_reference = models.CharField(max_length=100, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviewed_withdrawal_requests',
        null=True,
        blank=True,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-requested_at',)
        indexes = [
            models.Index(fields=('organizer', 'status', '-requested_at'), name='wd_org_status_request'),
        ]

    def __str__(self):
        return f'{self.organizer} {self.amount} {self.currency}'

    def clean(self):
        if self.amount and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': 'Withdrawal amount must be greater than zero.'})
        if self.wallet_account_id and self.organizer_id and self.wallet_account.owner_id != self.organizer_id:
            raise ValidationError({'wallet_account': 'Choose the organizer wallet account for this payout.'})
        if self.pk and self.status != self.Status.COMPLETED:
            try:
                self.ledger_transaction
            except LedgerTransaction.DoesNotExist:
                pass
            else:
                raise ValidationError(
                    'Completed withdrawals that have posted to the ledger cannot move back to another state.'
                )
        if (
            self.organizer_id
            and self.amount
            and self.status in {self.Status.APPROVED, self.Status.PROCESSING, self.Status.COMPLETED}
        ):
            from .services import get_available_withdrawal_balance

            available_balance = get_available_withdrawal_balance(
                self.organizer,
                exclude_withdrawal=self,
                exclude_pending=True,
            )
            if self.amount > available_balance:
                raise ValidationError(
                    {'amount': f'This withdrawal exceeds the available balance of {available_balance:.2f}.'}
                )

    @property
    def is_reserved(self):
        return self.status in {
            self.Status.APPROVED,
            self.Status.PROCESSING,
            self.Status.COMPLETED,
        }
