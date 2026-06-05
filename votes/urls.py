from django.urls import path
from .views import arkesel_ussd_callback

app_name = 'votes'

urlpatterns = [
    path('ussd/', arkesel_ussd_callback, name='ussd_callback'),
]
