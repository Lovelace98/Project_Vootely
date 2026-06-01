from django.urls import path

from .views import (
    PaymentStatusPanelView,
    PaymentStatusView,
    PaystackCallbackView,
    PaystackInitiateView,
    PaystackWebhookView,
)

app_name = 'payments'

urlpatterns = [
    path('paystack/initiate/', PaystackInitiateView.as_view(), name='paystack_initiate'),
    path('paystack/webhook/', PaystackWebhookView.as_view(), name='paystack_webhook'),
    path('paystack/callback/', PaystackCallbackView.as_view(), name='paystack_callback'),
    path('status/', PaymentStatusView.as_view(), name='status_lookup'),
    path('status/<str:reference>/', PaymentStatusView.as_view(), name='status_detail'),
    path('status/<str:reference>/panel/', PaymentStatusPanelView.as_view(), name='status_panel'),
]
