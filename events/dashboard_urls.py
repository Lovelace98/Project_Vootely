from django.urls import path

from accounts.views import (
    DashboardNotificationSettingsView,
    DashboardProfileView,
    DashboardNotificationsView,
    MarkAllNotificationsReadView,
    MarkNotificationReadView,
)
from nominees.views import (
    DashboardNomineeCreateView,
    DashboardNomineeDeleteView,
    DashboardNomineeUpdateView,
)
from wallets.views import (
    BankListPartialView,
    DashboardRevenueView,
    DashboardWithdrawalsView,
    RequestOTPView,
    ResolveAccountView,
    DashboardAnalyticsView,
)
from elections.views import (
    DashboardElectionActionView,
    DashboardElectionCandidatesView,
    DashboardElectionCreateView,
    DashboardElectionCredentialsView,
    DashboardElectionDetailView,
    DashboardElectionInvoiceView,
    DashboardElectionPositionsView,
    DashboardElectionRosterView,
    DashboardElectionUpdateView,
    DashboardElectionPositionUpdateView,
    DashboardElectionPositionDeleteView,
    DashboardElectionCandidateUpdateView,
    DashboardElectionCandidateDeleteView,
)

from .views import (
    DashboardEventActionView,
    DashboardEventCreateView,
    DashboardEventDetailView,
    DashboardEventUpdateView,
    DashboardHomeView,
    DashboardSearchView,
    DashboardCompetitionsListView,
    DashboardElectionsListView,
)

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardHomeView.as_view(), name='home'),
    path('competitions/', DashboardCompetitionsListView.as_view(), name='competitions'),
    path('elections/', DashboardElectionsListView.as_view(), name='elections'),
    path('my-events/', DashboardCompetitionsListView.as_view(), name='my_events'),
    path('profile/', DashboardProfileView.as_view(), name='profile'),
    path('search/', DashboardSearchView.as_view(), name='search'),
    path('analytics/', DashboardAnalyticsView.as_view(), name='analytics'),
    path('notifications/', DashboardNotificationsView.as_view(), name='notifications'),
    path('notifications/read-all/', MarkAllNotificationsReadView.as_view(), name='mark_all_read'),
    path('notifications/<int:pk>/read/', MarkNotificationReadView.as_view(), name='mark_read'),
    path('revenue/', DashboardRevenueView.as_view(), name='revenue'),
    path('withdrawals/', DashboardWithdrawalsView.as_view(), name='withdrawals'),
    path('withdrawals/resolve-account/', ResolveAccountView.as_view(), name='resolve_account'),
    path('withdrawals/bank-list/', BankListPartialView.as_view(), name='bank_list'),
    path('withdrawals/request-otp/', RequestOTPView.as_view(), name='request_otp'),
    path(
        'settings/notifications/',
        DashboardNotificationSettingsView.as_view(),
        name='notification_settings',
    ),
    path('events/new/', DashboardEventCreateView.as_view(), name='event_create'),
    path('elections/new/', DashboardElectionCreateView.as_view(), name='election_create'),
    path('elections/<slug:slug>/', DashboardElectionDetailView.as_view(), name='election_detail'),
    path('elections/<slug:slug>/edit/', DashboardElectionUpdateView.as_view(), name='election_edit'),
    path('elections/<slug:slug>/positions/', DashboardElectionPositionsView.as_view(), name='election_positions'),
    path(
        'elections/<slug:slug>/positions/<int:pk>/edit/',
        DashboardElectionPositionUpdateView.as_view(),
        name='election_position_edit',
    ),
    path(
        'elections/<slug:slug>/positions/<int:pk>/delete/',
        DashboardElectionPositionDeleteView.as_view(),
        name='election_position_delete',
    ),
    path('elections/<slug:slug>/candidates/', DashboardElectionCandidatesView.as_view(), name='election_candidates'),
    path(
        'elections/<slug:slug>/candidates/<int:pk>/edit/',
        DashboardElectionCandidateUpdateView.as_view(),
        name='election_candidate_edit',
    ),
    path(
        'elections/<slug:slug>/candidates/<int:pk>/delete/',
        DashboardElectionCandidateDeleteView.as_view(),
        name='election_candidate_delete',
    ),
    path('elections/<slug:slug>/roster/', DashboardElectionRosterView.as_view(), name='election_roster'),
    path('elections/<slug:slug>/invoice/', DashboardElectionInvoiceView.as_view(), name='election_invoice'),
    path('elections/<slug:slug>/credentials/', DashboardElectionCredentialsView.as_view(), name='election_credentials'),
    path(
        'elections/<slug:slug>/actions/<str:action>/',
        DashboardElectionActionView.as_view(),
        name='election_action',
    ),
    path('events/<slug:slug>/', DashboardEventDetailView.as_view(), name='event_detail'),
    path('events/<slug:slug>/edit/', DashboardEventUpdateView.as_view(), name='event_edit'),
    path(
        'events/<slug:slug>/actions/<str:action>/',
        DashboardEventActionView.as_view(),
        name='event_action',
    ),
    path(
        'events/<slug:event_slug>/nominees/new/',
        DashboardNomineeCreateView.as_view(),
        name='nominee_create',
    ),
    path(
        'events/<slug:event_slug>/nominees/<slug:slug>/edit/',
        DashboardNomineeUpdateView.as_view(),
        name='nominee_edit',
    ),
    path(
        'events/<slug:event_slug>/nominees/<slug:slug>/delete/',
        DashboardNomineeDeleteView.as_view(),
        name='nominee_delete',
    ),
]
