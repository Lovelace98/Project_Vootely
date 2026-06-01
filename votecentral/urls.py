from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from allauth.account.views import confirm_email

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/confirm-email/<str:key>/', confirm_email, name='account_confirm_email'),
    path('accounts/', include('allauth.urls')),
    path('payments/', include('payments.urls')),
    path('dashboard/', include('events.dashboard_urls')),
    path('', include('elections.urls')),
    path('', include('events.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
