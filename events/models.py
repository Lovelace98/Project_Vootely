import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import DatabaseError, models
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            kind__in=[
                Event.Kind.PAID_COMPETITION,
                Event.Kind.TICKETED_EVENT,
            ],
            status=Event.Status.PUBLISHED,
            is_public=True,
        )

    def active_public(self):
        now = timezone.now()
        return self.published().filter(start_at__lte=now, end_at__gte=now)


class Event(models.Model):
    PUBLIC_CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
    PUBLIC_CODE_LENGTH = 5
    PUBLIC_CODE_MAX_ATTEMPTS = 50
    USSD_CODE_MIN = 10
    USSD_CODE_MAX = 999
    USSD_CODE_MAX_ATTEMPTS = 100

    class Kind(models.TextChoices):
        PAID_COMPETITION = 'paid_competition', 'Paid competition'
        SECURE_ELECTION = 'secure_election', 'Secure election'
        TICKETED_EVENT = 'ticketed_event', 'Ticketed event'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        CONFIGURED = 'configured', 'Configured'
        ROSTER_UPLOADED = 'roster_uploaded', 'Roster uploaded'
        PRICED = 'priced', 'Priced'
        PAYMENT_PENDING = 'payment_pending', 'Payment pending'
        PAID = 'paid', 'Paid'
        ROSTER_LOCKED = 'roster_locked', 'Roster locked'
        CREDENTIALS_ISSUED = 'credentials_issued', 'Credentials issued'
        READY = 'ready', 'Ready'
        OPEN = 'open', 'Open'
        PUBLISHED = 'published', 'Published'
        CLOSED = 'closed', 'Closed'
        TALLIED = 'tallied', 'Tallied'
        CERTIFIED = 'certified', 'Certified'
        ARCHIVED = 'archived', 'Archived'
        CANCELLED = 'cancelled', 'Cancelled'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='events',
    )
    public_code = models.CharField(max_length=16, unique=True, blank=True, db_index=True)
    ussd_code = models.PositiveSmallIntegerField(
        unique=True,
        db_index=True,
        blank=True,
        validators=[
            MinValueValidator(USSD_CODE_MIN),
            MaxValueValidator(USSD_CODE_MAX),
        ],
    )
    title = models.CharField(max_length=160)
    slug = models.SlugField(unique=True, max_length=180, blank=True)
    description = models.TextField(blank=True)
    kind = models.CharField(
        max_length=24,
        choices=Kind.choices,
        default=Kind.PAID_COMPETITION,
    )
    banner = models.ImageField(upload_to='events/banners/', blank=True)
    flyer = models.ImageField(upload_to='events/flyers/', blank=True)
    currency = models.CharField(max_length=3, default='GHS')
    venue = models.CharField(max_length=200, blank=True, default='')
    event_date = models.DateTimeField(null=True, blank=True)
    vote_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        null=True,
        blank=True,
    )
    platform_commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.00')),
            MaxValueValidator(Decimal('100.00')),
        ],
        null=True,
        blank=True,
        help_text='Commission percentage retained by Vootely for this paid competition.',
    )
    platform_commission_set_at = models.DateTimeField(null=True, blank=True)
    platform_commission_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='configured_event_commissions',
        null=True,
        blank=True,
    )
    ticket_commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.00')),
            MaxValueValidator(Decimal('100.00')),
        ],
        default=Decimal('7.00'),
        help_text='Commission percentage retained by Vootely for paid ticket sales.',
    )
    ticket_commission_set_at = models.DateTimeField(null=True, blank=True)
    ticket_commission_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='configured_ticket_commissions',
        null=True,
        blank=True,
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_public = models.BooleanField(default=True)
    show_leaderboard = models.BooleanField(default=True)
    allow_public_nominations = models.BooleanField(default=False)
    nomination_start_at = models.DateTimeField(null=True, blank=True)
    nomination_end_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('owner', 'kind', 'status', 'start_at'), name='evt_owner_kind_stat_start'),
            models.Index(fields=('owner', 'kind', '-created_at'), name='evt_owner_kind_created'),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError('End date must be after the start date.')
        if self.kind != self.Kind.PAID_COMPETITION and (
            self.platform_commission_percent is not None
            or self.platform_commission_set_at
            or self.platform_commission_set_by_id
        ):
            raise ValidationError('Platform commission only applies to paid competitions.')
        if self.pk and self.ticket_commission_is_locked():
            previous_ticket_commission = (
                Event.objects.filter(pk=self.pk).values_list('ticket_commission_percent', flat=True).first()
            )
            if previous_ticket_commission != self.ticket_commission_percent:
                raise ValidationError('Ticket commission cannot change after the first successful ticket sale.')
        if self.kind != self.Kind.PAID_COMPETITION and self.allow_public_nominations:
            raise ValidationError('Public nominations are only available for paid competitions.')
        if self.allow_public_nominations:
            if not self.nomination_start_at or not self.nomination_end_at:
                raise ValidationError('Provide a nomination start and end time when public nominations are enabled.')
            if self.nomination_end_at <= self.nomination_start_at:
                raise ValidationError('Nomination end date must be after the nomination start date.')
        elif self.nomination_start_at or self.nomination_end_at:
            raise ValidationError('Enable public nominations before setting a nomination window.')
        if self.pk and self.commission_is_locked():
            previous_commission = (
                Event.objects.filter(pk=self.pk).values_list('platform_commission_percent', flat=True).first()
            )
            if previous_commission != self.platform_commission_percent:
                raise ValidationError('Platform commission cannot change after the first successful paid vote.')

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)[:170] or 'event'
            slug = base_slug
            counter = 2
            while Event.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug
        needs_public_code = not self.public_code
        if self.public_code:
            self.public_code = self.public_code.upper()
        if needs_public_code:
            self.public_code = self.generate_public_code()
        if not self.ussd_code:
            self.ussd_code = self.generate_ussd_code()
        super().save(*args, **kwargs)

    def generate_public_code(self):
        for _attempt in range(self.PUBLIC_CODE_MAX_ATTEMPTS):
            suffix = ''.join(
                secrets.choice(self.PUBLIC_CODE_ALPHABET)
                for _idx in range(self.PUBLIC_CODE_LENGTH)
            )
            code = f'V-{suffix}'
            if not Event.objects.exclude(pk=self.pk).filter(public_code=code).exists():
                return code
        raise ValidationError('Could not generate a unique event code. Please try again.')

    def generate_ussd_code(self):
        code_count = self.USSD_CODE_MAX - self.USSD_CODE_MIN + 1
        for _attempt in range(self.USSD_CODE_MAX_ATTEMPTS):
            code = self.USSD_CODE_MIN + secrets.randbelow(code_count)
            if not Event.objects.exclude(pk=self.pk).filter(ussd_code=code).exists():
                return code
        for code in range(self.USSD_CODE_MIN, self.USSD_CODE_MAX + 1):
            if not Event.objects.exclude(pk=self.pk).filter(ussd_code=code).exists():
                return code
        raise ValidationError('Could not generate a unique USSD code. Please try again.')

    @property
    def ussd_dial_code(self):
        if not self.ussd_code:
            return ''
        base_code = getattr(settings, 'USSD_SHORT_CODE', '*920*24#').strip()
        if not base_code:
            return ''
        base_without_hash = base_code[:-1] if base_code.endswith('#') else base_code
        return f'{base_without_hash}*{self.ussd_code}#'

    def accepts_votes(self, now=None):
        now = now or timezone.now()
        return (
            self.status == self.Status.PUBLISHED
            and self.is_public
            and self.has_platform_commission()
            and self.start_at <= now <= self.end_at
        )

    def has_platform_commission(self):
        return self.kind == self.Kind.PAID_COMPETITION and self.platform_commission_percent is not None

    def commission_rate_decimal(self):
        if not self.has_platform_commission():
            return Decimal('0.00')
        return (self.platform_commission_percent / Decimal('100')).quantize(Decimal('0.0001'))

    def commission_is_locked(self):
        if not self.pk or self.kind != self.Kind.PAID_COMPETITION:
            return False
        return self.payment_attempts.filter(status=self.payment_attempts.model.Status.PAID).exists()

    def ticket_commission_is_locked(self):
        if not self.pk:
            return False
        try:
            return self.ticket_purchases.filter(status='paid').exists()
        except DatabaseError:
            return False

    def accepts_tickets(self, now=None):
        now = now or timezone.now()
        if self.status != self.Status.PUBLISHED or not self.is_public:
            return False
        return self.ticket_types.filter(
            is_active=True,
            sale_start_at__lte=now,
            sale_end_at__gte=now,
        ).exists()

    def has_valid_nomination_window(self):
        return bool(
            self.allow_public_nominations
            and self.nomination_start_at
            and self.nomination_end_at
            and self.nomination_end_at > self.nomination_start_at
        )

    def accepts_nominations(self, now=None):
        now = now or timezone.now()
        return (
            self.kind == self.Kind.PAID_COMPETITION
            and self.is_public
            and self.has_valid_nomination_window()
            and self.status not in {
                self.Status.CLOSED,
                self.Status.ARCHIVED,
                self.Status.CANCELLED,
            }
            and self.nomination_start_at <= now <= self.nomination_end_at
        )

    def public_state(self, now=None):
        now = now or timezone.now()
        if self.status == self.Status.CLOSED:
            return 'closed'
        if self.status != self.Status.PUBLISHED or not self.is_public:
            return 'hidden'
        if now < self.start_at:
            return 'upcoming'
        if now > self.end_at:
            return 'ended'
        return 'active'

    def can_publish(self):
        errors = []
        if self.kind == self.Kind.TICKETED_EVENT:
            if not self.start_at or not self.end_at or self.end_at <= self.start_at:
                errors.append('Provide a valid event window.')
            if self.pk and not self.ticket_types.filter(is_active=True).exists():
                errors.append('Add at least one active ticket type.')
            elif not self.pk:
                errors.append('Save the event, then add at least one active ticket type before publishing.')
            return (len(errors) == 0, errors)
        if self.kind != self.Kind.PAID_COMPETITION:
            errors.append('Secure elections must be opened from the election workflow.')
            return (False, errors)
        if not self.vote_price or self.vote_price <= 0:
            errors.append('Set a positive vote price.')
        if not self.has_platform_commission():
            errors.append('Platform commission must be set by Vootely admin before publish.')
        if not self.start_at or not self.end_at or self.end_at <= self.start_at:
            errors.append('Provide a valid voting window.')
        if not self.competition_categories.filter(is_active=True).exists():
            errors.append('Add at least one active category.')
        if self.allow_public_nominations:
            if not self.has_valid_nomination_window():
                errors.append('Provide a valid nomination window.')
        elif not self.nominees.filter(is_active=True).exists():
            errors.append('Add at least one active nominee.')
        return (len(errors) == 0, errors)

    def publish(self):
        allowed, errors = self.can_publish()
        if not allowed:
            raise ValidationError(errors)

        self.status = self.Status.PUBLISHED
        if not self.published_at:
            self.published_at = timezone.now()
        self.save(update_fields=['status', 'published_at', 'updated_at'])

    def unpublish(self):
        self.status = self.Status.DRAFT
        self.save(update_fields=['status', 'updated_at'])

    def close(self):
        self.status = self.Status.CLOSED
        self.save(update_fields=['status', 'updated_at'])

    def get_absolute_url(self):
        return reverse('events:public_detail', args=[self.slug])

    def get_dashboard_url(self):
        if self.kind == self.Kind.TICKETED_EVENT:
            return reverse('dashboard:ticketed_event_detail', args=[self.slug])
        return reverse('dashboard:event_detail', args=[self.slug])

    @property
    def venue_display(self):
        return self.venue if self.venue else "To be announced"

    @property
    def event_date_display(self):
        if self.event_date:
            return self.event_date.strftime('%a, %d %b %Y, %I:%M %p')
        return "To be announced"

    @property
    def starting_price(self):
        if hasattr(self, '_starting_price'):
            return self._starting_price
        if not self.pk:
            return None
        active_tickets = self.ticket_types.filter(is_active=True)
        if active_tickets.exists():
            return min(t.price for t in active_tickets)
        return None

    @starting_price.setter
    def starting_price(self, value):
        self._starting_price = value


class ContactInquiry(models.Model):
    class HeardAboutUs(models.TextChoices):
        SOCIAL_MEDIA = 'social_media', 'Social media'
        FRIEND_REFERRAL = 'friend_referral', 'Friend referral'
        CAMPUS_EVENT = 'campus_event', 'Campus event'
        ORGANIZATION_REFERRAL = 'organization_referral', 'Organization referral'
        GOOGLE_SEARCH = 'google_search', 'Google search'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone_number = models.CharField(max_length=32)
    heard_about_us = models.CharField(max_length=32, choices=HeardAboutUs.choices)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('-created_at',), name='contact_inquiry_created'),
        ]

    def __str__(self):
        return f'{self.name} ({self.email})'


class VoteBundle(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='vote_bundles',
    )
    quantity = models.PositiveIntegerField(
        help_text='Number of votes included in this package.'
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Total price for this package in event currency.'
    )
    label = models.CharField(
        max_length=80,
        blank=True,
        help_text='Optional badge label (e.g. Save 10%, Popular).'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this package is active and selectable.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('quantity',)
        unique_together = ('event', 'quantity')

    def __str__(self):
        return f'{self.quantity} votes for {self.price} {self.event.currency}'
