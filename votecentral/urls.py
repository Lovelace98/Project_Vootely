from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from allauth.account.views import confirm_email

urlpatterns = [
    path('admin/', admin.site.urls),
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json'), name='manifest_json'),
    path('service-worker.js', TemplateView.as_view(template_name='service-worker.js', content_type='application/javascript'), name='service_worker_js'),
    path('accounts/confirm-email/<str:key>/', confirm_email, name='account_confirm_email'),
    path('accounts/', include('allauth.urls')),
    path('payments/', include('payments.urls')),
    path('dashboard/', include('events.dashboard_urls')),
    path('', include('elections.urls')),
    path('', include('events.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

