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
        indexes = [
            models.Index(fields=('event', '-paid_at'), name='vote_event_paid_at'),
            models.Index(fields=('nominee', '-paid_at'), name='vote_nominee_paid_at'),
            models.Index(fields=('event', 'nominee', '-paid_at'), name='vote_event_nominee_paid'),
        ]

    def __str__(self):
        return f'{self.nominee.name} x {self.quantity}'


class USSDSession(models.Model):
    class State(models.TextChoices):
        INITIATE = 'INITIATE', 'Initiate'
        ENTER_NOMINEE_CODE = 'ENTER_NOMINEE_CODE', 'Enter Nominee Code'
        ENTER_TICKET_EVENT_CODE = 'ENTER_TICKET_EVENT_CODE', 'Enter Ticket Event Code'
        SELECT_BUNDLE = 'SELECT_BUNDLE', 'Select Bundle'
        SELECT_TICKET_PURCHASE_FOR = 'SELECT_TICKET_PURCHASE_FOR', 'Select Ticket Purchase For'
        ENTER_RECIPIENT_PHONE = 'ENTER_RECIPIENT_PHONE', 'Enter Recipient Phone'
        CONFIRM_RECIPIENT_PHONE = 'CONFIRM_RECIPIENT_PHONE', 'Confirm Recipient Phone'
        SELECT_TICKET_TYPE = 'SELECT_TICKET_TYPE', 'Select Ticket Type'
        ENTER_TICKET_QUANTITY = 'ENTER_TICKET_QUANTITY', 'Enter Ticket Quantity'
        ENTER_VOTES = 'ENTER_VOTES', 'Enter Votes'
        ENTER_CUSTOM_VOTES = 'ENTER_CUSTOM_VOTES', 'Enter Custom Votes'
        CONFIRM_PAYMENT = 'CONFIRM_PAYMENT', 'Confirm Payment'
        CONFIRM_TICKET_PAYMENT = 'CONFIRM_TICKET_PAYMENT', 'Confirm Ticket Payment'

    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    phone_number = models.CharField(max_length=32, db_index=True)
    user_id = models.CharField(max_length=100, blank=True)
    current_state = models.CharField(
        max_length=50, choices=State.choices, default=State.INITIATE,
    )

    # State data cache
    nominee_id = models.IntegerField(null=True, blank=True)
    event_id = models.IntegerField(null=True, blank=True)
    ticket_type_id = models.IntegerField(null=True, blank=True)
    votes_count = models.PositiveIntegerField(null=True, blank=True)
    ticket_quantity = models.PositiveIntegerField(null=True, blank=True)
    purchase_for = models.CharField(max_length=16, blank=True, default='')
    recipient_phone = models.CharField(max_length=32, blank=True, default='')
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.phone_number} - {self.current_state}"
