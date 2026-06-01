from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            kind=Event.Kind.PAID_COMPETITION,
            status=Event.Status.PUBLISHED,
            is_public=True,
        )

    def active_public(self):
        now = timezone.now()
        return self.published().filter(start_at__lte=now, end_at__gte=now)


class Event(models.Model):
    class Kind(models.TextChoices):
        PAID_COMPETITION = 'paid_competition', 'Paid competition'
        SECURE_ELECTION = 'secure_election', 'Secure election'

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
        on_delete=models.CASCADE,
        related_name='events',
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
    vote_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
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

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)[:170] or 'event'
            slug = base_slug
            counter = 2
            while Event.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def accepts_votes(self, now=None):
        now = now or timezone.now()
        return (
            self.status == self.Status.PUBLISHED
            and self.is_public
            and self.start_at <= now <= self.end_at
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
        if self.kind != self.Kind.PAID_COMPETITION:
            errors.append('Secure elections must be opened from the election workflow.')
            return (False, errors)
        if not self.vote_price or self.vote_price <= 0:
            errors.append('Set a positive vote price.')
        if not self.start_at or not self.end_at or self.end_at <= self.start_at:
            errors.append('Provide a valid voting window.')
        if not self.nominees.filter(is_active=True).exists():
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
        return reverse('dashboard:event_detail', args=[self.slug])
