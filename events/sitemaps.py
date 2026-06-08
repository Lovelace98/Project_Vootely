from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from urllib.parse import urlparse
from django.contrib.sites.models import Site
from votecentral.public_urls import get_public_base_url
from events.models import Event
from nominees.models import Nominee

class VootelySitemap(Sitemap):
    def get_urls(self, page=1, site=None, protocol=None):
        base_url = get_public_base_url()
        if base_url:
            parsed = urlparse(base_url)
            domain = parsed.netloc
            protocol = parsed.scheme or 'https'
            site = Site(domain=domain, name=domain)
        return super().get_urls(page=page, site=site, protocol=protocol)

class StaticViewSitemap(VootelySitemap):
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

class EventSitemap(VootelySitemap):
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

class NomineeSitemap(VootelySitemap):
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


class BlogSitemap(VootelySitemap):
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        from events.blog_data import BLOG_POSTS
        return BLOG_POSTS

    def location(self, item):
        return reverse('events:blog_detail', args=[item['slug']])
