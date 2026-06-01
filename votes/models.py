from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class VotePurchase(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.PROTECT,
        related_name='vote_purchases',
    )
    nominee = models.ForeignKey(
        'nominees.Nominee',
        on_delete=models.PROTECT,
        related_name='vote_purchases',
    )
    payment_attempt = models.OneToOneField(
        'payments.PaymentAttempt',
        on_delete=models.PROTECT,
        related_name='vote_purchase',
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_reference = models.CharField(max_length=100, unique=True)
    voter_name = models.CharField(max_length=120, blank=True)
    voter_email = models.EmailField(blank=True)
    voter_phone = models.CharField(max_length=32, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-paid_at',)

    def __str__(self):
        return f'{self.nominee.name} x {self.quantity}'
