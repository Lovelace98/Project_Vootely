from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import TemplateView
from allauth.account.views import confirm_email

from events.sitemaps import StaticViewSitemap, EventSitemap, NomineeSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'events': EventSitemap,
    'nominees': NomineeSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain'), name='robots_txt'),
    path('llms.txt', TemplateView.as_view(template_name='llms.txt', content_type='text/plain'), name='llms_txt'),
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json'), name='manifest_json'),
    path('service-worker.js', TemplateView.as_view(template_name='service-worker.js', content_type='application/javascript'), name='service_worker_js'),
    path('accounts/confirm-email/<str:key>/', confirm_email, name='account_confirm_email'),
    path('accounts/', include('allauth.urls')),
    path('payments/', include('payments.urls')),
    path('dashboard/', include('events.dashboard_urls')),
    path('', include('elections.urls')),
    path('', include('events.urls')),
    path('', include('votes.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

