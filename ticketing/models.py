import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, Q, QuerySet
from django.urls import reverse
from django.utils import timezone


class TicketTypeQuerySet(QuerySet):
    def annotate_sold_count(self):
        return self.annotate(
            _sold_count=Count('tickets', filter=~Q(tickets__status__in=['cancelled', 'refunded'])),
        )


class TicketType(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='ticket_types',
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    quantity_available = models.PositiveIntegerField()
    sale_start_at = models.DateTimeField()
    sale_end_at = models.DateTimeField()
    max_per_order = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('price', 'name')
        constraints = [
            models.UniqueConstraint(fields=('event', 'name'), name='ticket_type_event_name_unique'),
        ]
        indexes = [
            models.Index(fields=('event', 'is_active', 'sale_start_at', 'sale_end_at'), name='tt_event_active_sale'),
        ]

    objects = TicketTypeQuerySet.as_manager()

    def __str__(self):
        return f'{self.event.title} - {self.name}'

    def clean(self):
        if self.sale_start_at and self.sale_end_at and self.sale_end_at <= self.sale_start_at:
            raise ValidationError({'sale_end_at': 'Sale end date must be after the sale start date.'})
        if self.event_id and self.name:
            duplicate_names = TicketType.objects.filter(event_id=self.event_id, name__iexact=self.name)
            if self.pk:
                duplicate_names = duplicate_names.exclude(pk=self.pk)
            if duplicate_names.exists():
                raise ValidationError({'name': 'A ticket type with this name already exists for this event.'})
        if self.event_id and self.quantity_available < self.quantity_sold:
            raise ValidationError({'quantity_available': 'Quantity available cannot be lower than tickets already sold.'})

    @property
    def quantity_sold(self):
        if not self.pk:
            return 0
        if hasattr(self, '_sold_count'):
            return self._sold_count
        return self.tickets.exclude(
            status__in=[Ticket.Status.CANCELLED, Ticket.Status.REFUNDED]
        ).count()

    @property
    def remaining_quantity(self):
        return max(self.quantity_available - self.quantity_sold, 0)

    def is_on_sale(self, now=None):
        now = now or timezone.now()
        return self.is_active and self.sale_start_at <= now <= self.sale_end_at

    def can_purchase(self, quantity, now=None):
        try:
            quantity = int(quantity or 0)
        except (TypeError, ValueError):
            return False, 'Choose a valid ticket quantity.'
        if quantity < 1:
            return False, 'Choose at least one ticket.'
        if quantity > self.max_per_order:
            return False, f'You can buy up to {self.max_per_order} ticket(s) per order.'
        if not self.is_on_sale(now=now):
            return False, 'This ticket type is not on sale right now.'
        if quantity > self.remaining_quantity:
            return False, 'Not enough tickets are available for this ticket type.'
        return True, ''


class TicketPurchase(models.Model):
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
        related_name='ticket_purchases',
    )
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        related_name='purchases',
    )
    gateway = models.CharField(
        max_length=24,
        choices=Gateway.choices,
        default=Gateway.PAYSTACK,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.INITIALIZED,
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    ticket_commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        default=Decimal('7.00'),
    )
    buyer_handling_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    buyer_name = models.CharField(max_length=120, blank=True)
    buyer_email = models.EmailField(blank=True)
    buyer_phone = models.CharField(max_length=32, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
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
        indexes = [
            models.Index(fields=('event', 'status', '-initiated_at'), name='tp_event_status_init'),
            models.Index(fields=('ticket_type', 'status'), name='tp_type_status'),
        ]

    def __str__(self):
        return self.gateway_reference

    @property
    def tickets_issued(self):
        return self.tickets.count()

    def get_absolute_url(self):
        return reverse('ticketing:purchase_detail', args=[self.gateway_reference])


class Ticket(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        USED = 'used', 'Used'
        REFUNDED = 'refunded', 'Refunded'
        CANCELLED = 'cancelled', 'Cancelled'

    purchase = models.ForeignKey(
        TicketPurchase,
        on_delete=models.PROTECT,
        related_name='tickets',
    )
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        related_name='tickets',
    )
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='tickets',
    )
    code = models.CharField(max_length=32, unique=True, blank=True)
    qr_data = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    used_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='checked_in_tickets',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('code',)
        indexes = [
            models.Index(fields=('event', 'status'), name='ticket_event_status'),
            models.Index(fields=('purchase', 'status'), name='ticket_purchase_status'),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_unique_code()
        if not self.qr_data:
            self.qr_data = self.code
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_code(cls):
        for _attempt in range(20):
            code = f'VT{secrets.token_hex(5).upper()}'
            if not cls.objects.filter(code=code).exists():
                return code
        return uuid.uuid4().hex.upper()

    def get_absolute_url(self):
        return reverse('ticketing:ticket_detail', args=[self.code])


def scanner_pass_default_expiry(event):
    return event.end_at + timezone.timedelta(hours=12)


class TicketScannerPass(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        REVOKED = 'revoked', 'Revoked'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='ticket_scanner_passes',
    )
    gate_name = models.CharField(max_length=80)
    staff_label = models.CharField(max_length=120, blank=True)
    token = models.CharField(max_length=64, unique=True, blank=True)
    pin_hash = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    allow_provisional_entry = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    device_session_key = models.CharField(max_length=80, blank=True)
    device_user_agent = models.TextField(blank=True)
    device_ip = models.GenericIPAddressField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='created_ticket_scanner_passes',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('gate_name', 'staff_label', '-created_at')
        indexes = [
            models.Index(fields=('event', 'status', 'expires_at'), name='scanner_event_status_exp'),
            models.Index(fields=('token', 'status'), name='scanner_token_status'),
        ]

    def __str__(self):
        label = self.staff_label or self.gate_name
        return f'{self.event.title} - {label}'

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generate_unique_token()
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_token(cls):
        for _attempt in range(30):
            token = secrets.token_urlsafe(24)
            if not cls.objects.filter(token=token).exists():
                return token
        return uuid.uuid4().hex

    def is_expired(self, now=None):
        now = now or timezone.now()
        return bool(self.expires_at and self.expires_at <= now)

    def can_activate(self, now=None):
        return self.status == self.Status.ACTIVE and not self.is_expired(now=now)

    def is_device_bound_to(self, session_key):
        return bool(self.device_session_key and session_key and self.device_session_key == session_key)

    def get_absolute_url(self):
        return reverse('ticketing:scanner_pass', args=[self.token])


class TicketCheckIn(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name='checkins',
    )
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='ticket_checkins',
    )
    scanned_at = models.DateTimeField(default=timezone.now)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='ticket_checkins',
        null=True,
        blank=True,
    )
    scanner_pass = models.ForeignKey(
        TicketScannerPass,
        on_delete=models.SET_NULL,
        related_name='checkins',
        null=True,
        blank=True,
    )
    scanner_gate_name = models.CharField(max_length=80, blank=True)
    scanner_staff_label = models.CharField(max_length=120, blank=True)
    scanner_ip = models.GenericIPAddressField(null=True, blank=True)
    scanner_user_agent = models.TextField(blank=True)
    status_before = models.CharField(max_length=16, blank=True)
    status_after = models.CharField(max_length=16, blank=True)
    message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ('-scanned_at',)
        indexes = [
            models.Index(fields=('event', '-scanned_at'), name='checkin_event_scanned'),
            models.Index(fields=('ticket', '-scanned_at'), name='checkin_ticket_scanned'),
            models.Index(fields=('scanner_pass', '-scanned_at'), name='checkin_pass_scanned'),
        ]

    def __str__(self):
        return f'{self.ticket.code} {self.status_before}->{self.status_after}'


class TicketProvisionalEntry(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending sync'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED = 'rejected', 'Rejected'

    class Result(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        DUPLICATE_REJECTED = 'duplicate_rejected', 'Duplicate rejected'
        WRONG_EVENT_REJECTED = 'wrong_event_rejected', 'Wrong event rejected'
        NOT_FOUND_REJECTED = 'not_found_rejected', 'Not found rejected'
        INACTIVE_REJECTED = 'inactive_rejected', 'Inactive rejected'
        UNAUTHORIZED_REJECTED = 'unauthorized_rejected', 'Unauthorized rejected'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='ticket_provisional_entries',
    )
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.SET_NULL,
        related_name='provisional_entries',
        null=True,
        blank=True,
    )
    scanner_pass = models.ForeignKey(
        TicketScannerPass,
        on_delete=models.SET_NULL,
        related_name='provisional_entries',
        null=True,
        blank=True,
    )
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='ticket_provisional_entries',
        null=True,
        blank=True,
    )
    final_checkin = models.ForeignKey(
        TicketCheckIn,
        on_delete=models.SET_NULL,
        related_name='provisional_entries',
        null=True,
        blank=True,
    )
    client_attempt_id = models.CharField(max_length=80, unique=True)
    ticket_code = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result = models.CharField(max_length=32, choices=Result.choices, default=Result.PENDING)
    message = models.CharField(max_length=255, blank=True)
    gate_name = models.CharField(max_length=80, blank=True)
    staff_label = models.CharField(max_length=120, blank=True)
    device_id = models.CharField(max_length=80, blank=True)
    offline_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    scanner_ip = models.GenericIPAddressField(null=True, blank=True)
    scanner_user_agent = models.TextField(blank=True)
    cached_ticket_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('event', '-created_at'), name='prov_event_created'),
            models.Index(fields=('scanner_pass', '-created_at'), name='prov_pass_created'),
            models.Index(fields=('status', 'result'), name='prov_status_result'),
        ]

    def __str__(self):
        return f'{self.ticket_code} {self.result}'
