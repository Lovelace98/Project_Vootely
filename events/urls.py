from django.urls import path

from nominees.views import PublicNominationCreateView, PublicNomineeDetailView, PublicNomineePaymentStatusView

from .views import (
    EventDetailView,
    EventLeaderboardPartialView,
    EventLiveLeaderboardView,
    LandingContactInquiryCreateView,
    LandingPageView,
    HomeView,
    PrivacyPolicyView,
    TermsOfServiceView,
    OrganizerAgreementView,
    BlogListView,
    BlogDetailView,
)

app_name = 'events'

urlpatterns = [
    path('', LandingPageView.as_view(), name='landing'),
    path('contact/', LandingContactInquiryCreateView.as_view(), name='contact_inquiry_submit'),
    path('events/', HomeView.as_view(), name='home'),
    path('privacy/', PrivacyPolicyView.as_view(), name='privacy_policy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms_of_service'),
    path('organizer-agreement/', OrganizerAgreementView.as_view(), name='organizer_agreement'),
    path('blog/', BlogListView.as_view(), name='blog_list'),
    path('blog/<slug:slug>/', BlogDetailView.as_view(), name='blog_detail'),
    path('events/<slug:slug>/', EventDetailView.as_view(), name='public_detail'),
    path(
        'events/<slug:slug>/leaderboard/',
        EventLeaderboardPartialView.as_view(),
        name='leaderboard',
    ),
    path(
        'events/<slug:slug>/leaderboard/live/',
        EventLiveLeaderboardView.as_view(),
        name='leaderboard_live',
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
    path(
        'events/<slug:event_slug>/nominate/',
        PublicNominationCreateView.as_view(),
        name='nominate',
    ),
]
