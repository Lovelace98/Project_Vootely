from django.urls import path

from nominees.views import PublicNomineeDetailView, PublicNomineePaymentStatusView

from .views import (
    EventDetailView,
    EventLeaderboardPartialView,
    HomeView,
    PrivacyPolicyView,
    TermsOfServiceView,
    OrganizerAgreementView,
)

app_name = 'events'

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('privacy/', PrivacyPolicyView.as_view(), name='privacy_policy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms_of_service'),
    path('organizer-agreement/', OrganizerAgreementView.as_view(), name='organizer_agreement'),
    path('events/<slug:slug>/', EventDetailView.as_view(), name='public_detail'),
    path(
        'events/<slug:slug>/leaderboard/',
        EventLeaderboardPartialView.as_view(),
        name='leaderboard',
    ),
    path(
        'events/<slug:event_slug>/nominees/<str:nominee_ref>/',
        PublicNomineeDetailView.as_view(),
        name='nominee_detail',
    ),
    path(
        'events/<slug:event_slug>/nominees/<str:nominee_ref>/payment-status/',
        PublicNomineePaymentStatusView.as_view(),
        name='nominee_payment_status',
    ),
]

