import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone


def generate_vote_code():
    import random
    import string
    # 5-character code excluding confusing characters (0, O, 1, I, L)
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(random.choices(chars, k=5))



class CompetitionCategory(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='competition_categories',
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('display_order', 'name')
        constraints = [
            models.UniqueConstraint(
                fields=('event', 'slug'),
                name='unique_category_slug_per_event',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.event.title})'

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:150] or 'category'
            slug = base_slug
            counter = 2
            while CompetitionCategory.objects.exclude(pk=self.pk).filter(event=self.event, slug=slug).exists():
                slug = f'{base_slug[:146]}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Nominee(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='nominees',
    )
    category = models.ForeignKey(
        'nominees.CompetitionCategory',
        on_delete=models.PROTECT,
        related_name='nominees',
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, blank=True)
    code = models.CharField(max_length=12, unique=True, default=generate_vote_code, editable=False)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='nominees/photos/', blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('display_order', 'name')
        constraints = [
            models.UniqueConstraint(fields=('event', 'slug'), name='unique_nominee_slug_per_event'),
        ]

    def __str__(self):
        return f'{self.name} ({self.event.title})'

    def clean(self):
        if self.category_id and self.event_id and self.category.event_id != self.event_id:
            raise ValidationError({'category': 'Choose a category that belongs to this event.'})

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:170] or 'nominee'
            slug = base_slug
            counter = 2
            while Nominee.objects.exclude(pk=self.pk).filter(event=self.event, slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug

        if not self.code:
            while True:
                candidate_code = generate_vote_code()
                if not Nominee.objects.filter(code=candidate_code).exists():
                    self.code = candidate_code
                    break
        else:
            if not self.pk and Nominee.objects.filter(code=self.code).exists():
                while True:
                    candidate_code = generate_vote_code()
                    if not Nominee.objects.filter(code=candidate_code).exists():
                        self.code = candidate_code
                        break

        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('events:nominee_detail', args=[self.event.slug, self.slug])

    @classmethod
    def resolve_for_event(cls, event, reference):
        return cls.objects.get(
            Q(event=event),
            Q(slug=reference) | Q(code__iexact=reference),
        )


class NominationSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='nomination_submissions',
    )
    category = models.ForeignKey(
        'nominees.CompetitionCategory',
        on_delete=models.PROTECT,
        related_name='nomination_submissions',
    )
    name = models.CharField(max_length=160)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='nomination-submissions/photos/', blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_nominee = models.OneToOneField(
        'nominees.Nominee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_submission',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('event', 'status', '-created_at'), name='nomsub_event_status_created'),
            models.Index(fields=('category', 'status', '-created_at'), name='nomsub_cat_status_created'),
            models.Index(fields=('email',), name='nomsub_email'),
            models.Index(fields=('phone_number',), name='nomsub_phone'),
        ]

    def __str__(self):
        return f'{self.name} ({self.event.title} / {self.category.name})'

    def clean(self):
        errors = {}
        if self.category_id and self.event_id and self.category.event_id != self.event_id:
            errors['category'] = 'Choose a category that belongs to this event.'
        if self.event_id and self.event.kind != self.event.Kind.PAID_COMPETITION:
            errors['event'] = 'Only paid competitions accept public nominations.'
        if self.status == self.Status.APPROVED and not self.approved_nominee_id:
            errors['approved_nominee'] = 'An approved nominee must be linked before marking this submission approved.'
        if self.status != self.Status.APPROVED and self.approved_nominee_id:
            errors['approved_nominee'] = 'Only approved submissions can link to an approved nominee.'
        if self.approved_nominee_id:
            if self.approved_nominee.event_id != self.event_id:
                errors['approved_nominee'] = 'Approved nominee must belong to the same event.'
            elif self.approved_nominee.category_id != self.category_id:
                errors['approved_nominee'] = 'Approved nominee must belong to the selected category.'
        duplicate_q = self._duplicate_queryset()
        if duplicate_q.exists():
            message = 'A pending or approved submission already exists for this contact in this category.'
            if self.email:
                errors['email'] = message
            if self.phone_number:
                errors['phone_number'] = message
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.status in {self.Status.APPROVED, self.Status.REJECTED} and not self.reviewed_at:
            self.reviewed_at = timezone.now()
        if self.status == self.Status.PENDING:
            self.reviewed_at = None
            self.review_notes = self.review_notes or ''
            self.approved_nominee = None
        super().save(*args, **kwargs)

    def _duplicate_queryset(self):
        if not self.event_id or not self.category_id:
            return self.__class__.objects.none()
        query = self.__class__.objects.exclude(pk=self.pk).filter(
            event_id=self.event_id,
            category_id=self.category_id,
            status__in=[self.Status.PENDING, self.Status.APPROVED],
        )
        contact_filter = Q()
        if self.email:
            contact_filter |= Q(email__iexact=self.email)
        if self.phone_number:
            contact_filter |= Q(phone_number=self.phone_number)
        if not contact_filter:
            return self.__class__.objects.none()
        return query.filter(contact_filter)
