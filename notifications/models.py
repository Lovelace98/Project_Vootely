from django.db import models


class Notification(models.Model):
    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        PROCESSING = 'processing', 'Processing'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    class EventType(models.TextChoices):
        PAYMENT_CONFIRMED = 'payment_confirmed', 'Payment Confirmed'
        PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
        PAYMENT_CANCELLED = 'payment_cancelled', 'Payment Cancelled'
        WITHDRAWAL_REQUESTED = 'withdrawal_requested', 'Withdrawal Requested'
        WITHDRAWAL_APPROVED = 'withdrawal_approved', 'Withdrawal Approved'
        WITHDRAWAL_PROCESSING = 'withdrawal_processing', 'Withdrawal Processing'
        WITHDRAWAL_COMPLETED = 'withdrawal_completed', 'Withdrawal Completed'
        WITHDRAWAL_REJECTED = 'withdrawal_rejected', 'Withdrawal Rejected'
        WITHDRAWAL_REVIEW_REQUIRED = 'withdrawal_review_required', 'Withdrawal Review Required'
        EVENT_PUBLISHED = 'event_published', 'Event Published'
        EVENT_CLOSED = 'event_closed', 'Event Closed'
        EVENT_COMMISSION_SETUP_REQUIRED = 'event_commission_setup_required', 'Event Commission Setup Required'
        EVENT_STARTING_SOON = 'event_starting_soon', 'Event Starting Soon'
        EVENT_ENDING_SOON = 'event_ending_soon', 'Event Ending Soon'
        ORGANIZER_WELCOME = 'organizer_welcome', 'Welcome Organizer'
        NOMINEE_GOES_LIVE = 'nominee_goes_live', 'Nominee Goes Live'
        NOMINEE_EVENT_CLOSED = 'nominee_event_closed', 'Nominee Event Closed'
        NOMINATION_SUBMITTED = 'nomination_submitted', 'Nomination Submitted'
        NOMINATION_APPROVED = 'nomination_approved', 'Nomination Approved'
        NOMINATION_REJECTED = 'nomination_rejected', 'Nomination Rejected'
        CANDIDATE_CONFIRMED = 'candidate_confirmed', 'Candidate Confirmed'
        CANDIDATE_ELECTION_CLOSED = 'candidate_election_closed', 'Candidate Election Closed'
        VOTER_CREDENTIALS = 'voter_credentials', 'Voter Credentials'
        VOTER_ELECTION_CLOSED = 'voter_election_closed', 'Voter Election Closed'
        VOTER_BALLOT_CAST = 'voter_ballot_cast', 'Voter Ballot Cast'
        VOTER_TURNOUT_REMINDER = 'voter_turnout_reminder', 'Voter Turnout Reminder'

    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.EMAIL,
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    recipient_name = models.CharField(max_length=120, blank=True)
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    payment_attempt = models.ForeignKey(
        'payments.PaymentAttempt',
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    withdrawal_request = models.ForeignKey(
        'wallets.WithdrawalRequest',
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=255, unique=True)
    provider = models.CharField(max_length=32, blank=True)
    provider_status = models.CharField(max_length=64, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    provider_error_code = models.CharField(max_length=64, blank=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    failure_reason = models.TextField(blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-queued_at',)

    def __str__(self):
        recipient = self.recipient_email or self.recipient_phone or 'unknown-recipient'
        return f'{self.event_type} -> {recipient}'


from django.conf import settings

class InAppNotification(models.Model):
    class Level(models.TextChoices):
        INFO = 'info', 'Info'
        SUCCESS = 'success', 'Success'
        WARNING = 'warning', 'Warning'
        DANGER = 'danger', 'Danger'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='in_app_notifications',
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    level = models.CharField(
        max_length=10,
        choices=Level.choices,
        default=Level.INFO,
    )
    
    # Audit log context relationships
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='in_app_logs',
    )
    nominee = models.ForeignKey(
        'nominees.Nominee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='in_app_logs',
    )
    payment_attempt = models.ForeignKey(
        'payments.PaymentAttempt',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='in_app_logs',
    )
    withdrawal_request = models.ForeignKey(
        'wallets.WithdrawalRequest',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='in_app_logs',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('user', 'is_read', '-created_at'), name='inapp_user_read_created'),
        ]

    def __str__(self):
        return f'{self.user.email} - {self.title}'
