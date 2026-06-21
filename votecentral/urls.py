from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.template import loader
from django.urls import include, path
from allauth.account.views import confirm_email

from events.sitemaps import (
    StaticViewSitemap,
    CompetitionSitemap,
    TicketedEventSitemap,
    ElectionSitemap,
    NomineeSitemap,
    BlogSitemap,
)


def service_worker_js(request):
    file_path = finders.find('js/service-worker.js')
    if file_path is None:
        file_path = settings.STATIC_ROOT / 'js' / 'service-worker.js'
        if not file_path.exists():
            return HttpResponse(status=404)
    with open(file_path, 'r') as f:
        content = f.read()
    return HttpResponse(content, content_type='application/javascript')


def lightweight_template_view(request, template_name, content_type):
    template = loader.get_template(template_name)
    context = {'request': request}
    content = template.render(context)
    return HttpResponse(content, content_type=content_type)

sitemaps = {
    'static': StaticViewSitemap,
    'competitions': CompetitionSitemap,
    'ticketed_events': TicketedEventSitemap,
    'elections': ElectionSitemap,
    'nominees': NomineeSitemap,
    'blog': BlogSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', lambda r: lightweight_template_view(r, 'robots.txt', 'text/plain'), name='robots_txt'),
    path('llms.txt', lambda r: lightweight_template_view(r, 'llms.txt', 'text/plain'), name='llms_txt'),
    path('manifest.json', lambda r: lightweight_template_view(r, 'manifest.json', 'application/json'), name='manifest_json'),
    path('service-worker.js', service_worker_js, name='service_worker_js'),
    path('accounts/confirm-email/<str:key>/', confirm_email, name='account_confirm_email'),
    path('accounts/', include('allauth.urls')),
    path('payments/', include('payments.urls')),
    path('tickets/', include('ticketing.urls')),
    path('dashboard/', include('events.dashboard_urls')),
    path('', include('elections.urls')),
    path('', include('events.urls')),
    path('', include('votes.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
