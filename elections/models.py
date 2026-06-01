import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone


def generate_anonymous_id():
    return uuid.uuid4().hex


class ElectionConfig(models.Model):
    class ResultsVisibility(models.TextChoices):
        HIDDEN = 'hidden', 'Hidden until organizer publishes'
        AFTER_CLOSE = 'after_close', 'Automatically visible after close'
        AFTER_TALLY = 'after_tally', 'Automatically visible after tally'

    event = models.OneToOneField(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_config',
    )
    results_visibility = models.CharField(
        max_length=16,
        choices=ResultsVisibility.choices,
        default=ResultsVisibility.HIDDEN,
    )
    allow_abstain = models.BooleanField(default=False)
    receipt_verification_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Election config for {self.event}'


class ElectionPosition(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_positions',
    )
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, blank=True)
    max_choices = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('display_order', 'title')
        constraints = [
            models.UniqueConstraint(fields=('event', 'slug'), name='unique_election_position_slug'),
        ]

    def save(self, *args, **kwargs):
        if self.event.status in {
            'credentials_issued',
            'ready',
            'open',
            'closed',
            'tallied',
            'certified',
            'archived',
        }:
            raise ValidationError('Election setup cannot be changed once credentials have been issued or voting has started.')
        if not self.slug:
            base_slug = slugify(self.title)[:170] or 'position'
            slug = base_slug
            counter = 2
            while ElectionPosition.objects.exclude(pk=self.pk).filter(event=self.event, slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.event.status in {
            'credentials_issued',
            'ready',
            'open',
            'closed',
            'tallied',
            'certified',
            'archived',
        }:
            raise ValidationError('Election setup cannot be changed once credentials have been issued or voting has started.')
        super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.title} ({self.event.title})'


class ElectionCandidate(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_candidates',
    )
    position = models.ForeignKey(
        ElectionPosition,
        on_delete=models.CASCADE,
        related_name='candidates',
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, blank=True)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='elections/candidates/', blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('position__display_order', 'display_order', 'name')
        constraints = [
            models.UniqueConstraint(fields=('position', 'slug'), name='unique_election_candidate_slug'),
        ]

    def save(self, *args, **kwargs):
        if self.position_id and not self.event_id:
            self.event = self.position.event
        if self.event and self.event.status in {
            'credentials_issued',
            'ready',
            'open',
            'closed',
            'tallied',
            'certified',
            'archived',
        }:
            raise ValidationError('Election setup cannot be changed once credentials have been issued or voting has started.')
        if not self.slug:
            base_slug = slugify(self.name)[:170] or 'candidate'
            slug = base_slug
            counter = 2
            while ElectionCandidate.objects.exclude(pk=self.pk).filter(position=self.position, slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.event and self.event.status in {
            'credentials_issued',
            'ready',
            'open',
            'closed',
            'tallied',
            'certified',
            'archived',
        }:
            raise ValidationError('Election setup cannot be changed once credentials have been issued or voting has started.')
        super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.name} for {self.position.title}'


class ElectionVoter(models.Model):
    class Status(models.TextChoices):
        ELIGIBLE = 'eligible', 'Eligible'
        REVOKED = 'revoked', 'Revoked'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_voters',
    )
    external_id = models.CharField(max_length=120)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ELIGIBLE)
    row_hash = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('external_id',)
        constraints = [
            models.UniqueConstraint(fields=('event', 'external_id'), name='unique_election_voter_external_id'),
        ]

    def __str__(self):
        return f'{self.name} ({self.external_id})'


class ElectionCredential(models.Model):
    class Status(models.TextChoices):
        CREATED = 'created', 'Created'
        ISSUED = 'issued', 'Issued'
        OPENED = 'opened', 'Opened'
        USED = 'used', 'Used'
        REVOKED = 'revoked', 'Revoked'
        REISSUED = 'reissued', 'Reissued'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_credentials',
    )
    voter = models.ForeignKey(
        ElectionVoter,
        on_delete=models.CASCADE,
        related_name='credentials',
    )
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    issued_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    reissued_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reissues',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def mark_opened(self):
        if self.status == self.Status.ISSUED:
            self.status = self.Status.OPENED
            self.opened_at = timezone.now()
            self.save(update_fields=['status', 'opened_at'])

    def __str__(self):
        return f'{self.voter} credential'


class ElectionCredentialExport(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='credential_exports',
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    row_count = models.PositiveIntegerField(default=0)
    rows = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)


class ElectionPricingPlan(models.Model):
    name = models.CharField(max_length=120, unique=True)
    currency = models.CharField(max_length=3, default='GHS')
    minimum_fee = models.DecimalField(max_digits=10, decimal_places=2, default='150.00')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ElectionPricingTier(models.Model):
    plan = models.ForeignKey(
        ElectionPricingPlan,
        on_delete=models.CASCADE,
        related_name='tiers',
    )
    start_count = models.PositiveIntegerField()
    end_count = models.PositiveIntegerField(null=True, blank=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ('start_count',)

    def __str__(self):
        end = self.end_count or '+'
        return f'{self.start_count}-{end}: {self.rate}'


class ElectionInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='election_invoices',
    )
    pricing_plan = models.ForeignKey(
        ElectionPricingPlan,
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    voter_count = models.PositiveIntegerField(default=0)
    covered_voter_count = models.PositiveIntegerField(default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default='0.00')
    currency = models.CharField(max_length=3, default='GHS')
    is_top_up = models.BooleanField(default=False)
    price_snapshot = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.event.title} invoice {self.amount} {self.currency}'

    def mark_paid(self, amount=None):
        self.status = self.Status.PAID
        self.amount_paid = amount or self.amount
        self.covered_voter_count = self.voter_count
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'amount_paid', 'covered_voter_count', 'paid_at', 'updated_at'])


class OrganizerPaymentAttempt(models.Model):
    class Gateway(models.TextChoices):
        PAYSTACK = 'paystack', 'Paystack'

    class Status(models.TextChoices):
        INITIALIZED = 'initialized', 'Initialized'
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='organizer_payment_attempts',
    )
    invoice = models.ForeignKey(
        ElectionInvoice,
        on_delete=models.PROTECT,
        related_name='payment_attempts',
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    gateway = models.CharField(max_length=24, choices=Gateway.choices, default=Gateway.PAYSTACK)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INITIALIZED)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    payer_email = models.EmailField()
    gateway_reference = models.CharField(max_length=100, unique=True)
    gateway_access_code = models.CharField(max_length=120, blank=True)
    gateway_checkout_url = models.URLField(blank=True)
    gateway_status = models.CharField(max_length=32, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    webhook_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    initiated_at = models.DateTimeField(auto_now_add=True)
    callback_received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    confirmed_webhook_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-initiated_at',)

    def __str__(self):
        return self.gateway_reference


class Ballot(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='ballots',
    )
    anonymous_id = models.CharField(max_length=32, unique=True, default=generate_anonymous_id)
    receipt_hash = models.CharField(max_length=64, unique=True)
    cast_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-cast_at',)

    def __str__(self):
        return f'Ballot {self.anonymous_id}'


class BallotSelection(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='selections')
    position = models.ForeignKey(ElectionPosition, on_delete=models.PROTECT)
    candidate = models.ForeignKey(ElectionCandidate, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('ballot', 'position', 'candidate'), name='unique_ballot_position_candidate'),
        ]


class BallotReceipt(models.Model):
    ballot = models.OneToOneField(Ballot, on_delete=models.CASCADE, related_name='receipt')
    code = models.CharField(max_length=32, unique=True)
    code_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_absolute_url(self):
        return reverse('elections:receipt', args=[self.ballot.event.slug, self.code])


class ElectionAuditLog(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='election_audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)


class ElectionTallySnapshot(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='tally_snapshots',
    )
    totals = models.JSONField(default=dict)
    ballot_count = models.PositiveIntegerField(default=0)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    generated_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-generated_at',)
