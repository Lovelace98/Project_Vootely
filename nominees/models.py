import uuid

from django.db import models
from django.db.models import Q
from django.template.defaultfilters import slugify
from django.urls import reverse


def generate_vote_code():
    return uuid.uuid4().hex[:8].upper()


class Nominee(models.Model):
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='nominees',
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, blank=True)
    code = models.CharField(max_length=12, unique=True, default=generate_vote_code, editable=False)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='nominees/photos/', blank=True)
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

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:170] or 'nominee'
            slug = base_slug
            counter = 2
            while Nominee.objects.exclude(pk=self.pk).filter(event=self.event, slug=slug).exists():
                slug = f'{base_slug[:166]}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('events:nominee_detail', args=[self.event.slug, self.slug])

    @classmethod
    def resolve_for_event(cls, event, reference):
        return cls.objects.get(
            Q(event=event),
            Q(slug=reference) | Q(code__iexact=reference),
        )
