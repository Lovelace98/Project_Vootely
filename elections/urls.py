from django.urls import path

from .views import (
    PublicElectionDetailView,
    PublicElectionReceiptView,
    PublicElectionResultsView,
    PublicElectionVoteView,
    PublicElectionVerifyReceiptView,
)

app_name = 'elections'

urlpatterns = [
    path('elections/<slug:slug>/', PublicElectionDetailView.as_view(), name='detail'),
    path('elections/<slug:slug>/vote/', PublicElectionVoteView.as_view(), name='vote'),
    path('elections/<slug:slug>/receipt/<str:receipt_code>/', PublicElectionReceiptView.as_view(), name='receipt'),
    path('elections/<slug:slug>/results/', PublicElectionResultsView.as_view(), name='results'),
    path('elections/<slug:slug>/results/verify/', PublicElectionVerifyReceiptView.as_view(), name='verify_receipt'),
]
