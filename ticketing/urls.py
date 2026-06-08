from django.urls import path

from .views import (
    PublicTicketScannerPassProvisionalSyncView,
    PublicTicketScannerPassScanView,
    PublicTicketScannerPassView,
    PublicTicketDetailView,
    PublicTicketQrView,
    PublicTicketPurchaseDetailView,
    TicketPurchaseInitiateView,
    TicketPurchaseStatusPanelView,
)

app_name = 'ticketing'

urlpatterns = [
    path('paystack/initiate/', TicketPurchaseInitiateView.as_view(), name='paystack_initiate'),
    path('purchases/<str:reference>/', PublicTicketPurchaseDetailView.as_view(), name='purchase_detail'),
    path('purchases/<str:reference>/panel/', TicketPurchaseStatusPanelView.as_view(), name='purchase_status_panel'),
    path('check-in/<str:token>/', PublicTicketScannerPassView.as_view(), name='scanner_pass'),
    path('check-in/<str:token>/scan/', PublicTicketScannerPassScanView.as_view(), name='scanner_pass_scan'),
    path('check-in/<str:token>/provisional-sync/', PublicTicketScannerPassProvisionalSyncView.as_view(), name='scanner_pass_provisional_sync'),
    path('<str:code>/qr.svg', PublicTicketQrView.as_view(), name='ticket_qr'),
    path('<str:code>/', PublicTicketDetailView.as_view(), name='ticket_detail'),
]
