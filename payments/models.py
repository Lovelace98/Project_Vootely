from django.core.validators import MinValueValidator
from django.db import models


class PaymentAttempt(models.Model):
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
        related_name='payment_attempts',
    )
    nominee = models.ForeignKey(
        'nominees.Nominee',
        on_delete=models.PROTECT,
        related_name='payment_attempts',
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
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    vote_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    voter_name = models.CharField(max_length=120, blank=True)
    voter_email = models.EmailField(blank=True)
    voter_phone = models.CharField(max_length=32, blank=True)
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
            models.Index(fields=('event', 'status', '-initiated_at'), name='pay_event_status_init'),
            models.Index(fields=('event', 'nominee', 'status'), name='pay_event_nom_status'),
        ]

    def __str__(self):
        return self.gateway_reference
