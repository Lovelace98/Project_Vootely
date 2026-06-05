from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from events.models import Event
from nominees.models import Nominee

class StaticViewSitemap(Sitemap):
    priority = 0.5
    changefreq = 'weekly'

    def items(self):
        return [
            'events:landing',
            'events:home',
            'events:privacy_policy',
            'events:terms_of_service',
            'events:organizer_agreement',
        ]

    def location(self, item):
        return reverse(item)

class EventSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.8

    def items(self):
        return Event.objects.filter(
            kind=Event.Kind.PAID_COMPETITION,
            is_public=True,
            status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        )

    def lastmod(self, obj):
        return obj.updated_at

class NomineeSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.7

    def items(self):
        return Nominee.objects.filter(
            is_active=True,
            event__kind=Event.Kind.PAID_COMPETITION,
            event__is_public=True,
            event__status__in=[Event.Status.PUBLISHED, Event.Status.CLOSED],
        ).select_related('event')

    def lastmod(self, obj):
        return obj.updated_at
