from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from elections.models import Ballot, ElectionCredential, ElectionVoter
from events.models import Event
from nominees.models import CompetitionCategory, Nominee
from notifications.models import InAppNotification, Notification
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from votecentral.admin_dashboard import build_dashboard_context
from votecentral.admin_utils import export_selected_as_csv
from votecentral.public_urls import build_public_url
from wallets.models import WithdrawalRequest
from wallets.services import get_organizer_account, post_payment_ledger_transaction


class AdvancedAdminTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.staff = self.user_model.objects.create_superuser(
            email='admin@example.com',
            password='strong-pass-123',
        )
        self.organizer = self.user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )

    def create_competition(self, **overrides):
        now = timezone.now()
        data = {
            'owner': self.organizer,
            'title': 'Campus Awards',
            'description': 'Admin test event',
            'kind': Event.Kind.PAID_COMPETITION,
            'currency': 'GHS',
            'vote_price': Decimal('2.50'),
            'start_at': now - timedelta(hours=1),
            'end_at': now + timedelta(days=1),
            'status': Event.Status.PUBLISHED,
            'is_public': True,
            'published_at': now - timedelta(hours=1),
        }
        data.update(overrides)
        return Event.objects.create(**data)

    def create_paid_purchase(self, amount=Decimal('20.00'), quantity=8):
        event = self.create_competition()
        category, _ = CompetitionCategory.objects.get_or_create(event=event, name='Test Category')
        nominee = Nominee.objects.create(event=event, category=category, name='Ama Mensah', is_active=True)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency='GHS',
            platform_commission_percent=Decimal('10.00'),
            vote_quantity=quantity,
            voter_name='Guest Buyer',
            voter_email='buyer@example.com',
            voter_phone='0240000000',
            gateway_reference='admin-paid-ref',
            status=PaymentAttempt.Status.PAID,
            completed_at=timezone.now(),
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee,
            payment_attempt=attempt,
            quantity=quantity,
            amount_paid=amount,
            currency='GHS',
            payment_reference=attempt.gateway_reference,
            paid_at=timezone.now(),
        )
        post_payment_ledger_transaction(attempt)
        return event, nominee, attempt

    def test_all_local_models_are_registered_in_admin(self):
        app_labels = {
            'accounts',
            'events',
            'elections',
            'nominees',
            'votes',
            'payments',
            'wallets',
            'notifications',
        }
        missing = [
            model._meta.label
            for model in apps.get_models()
            if model._meta.app_label in app_labels and not admin.site.is_registered(model)
        ]

        self.assertEqual(missing, [])

    def test_admin_index_requires_staff_and_renders_for_staff(self):
        self.client.force_login(self.organizer)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vootely Operations')

    def test_dashboard_context_reports_core_kpis(self):
        self.create_paid_purchase(amount=Decimal('20.00'), quantity=8)
        now = timezone.now()
        secure_event = Event.objects.create(
            owner=self.organizer,
            title='SRC Election',
            kind=Event.Kind.SECURE_ELECTION,
            currency='GHS',
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1),
            status=Event.Status.OPEN,
            is_public=True,
        )
        voter = ElectionVoter.objects.create(
            event=secure_event,
            external_id='V001',
            name='Voter One',
            email='voter@example.com',
        )
        ElectionCredential.objects.create(
            event=secure_event,
            voter=voter,
            token_hash='admin-test-token-hash',
            status=ElectionCredential.Status.USED,
            used_at=timezone.now(),
        )
        Ballot.objects.create(event=secure_event, receipt_hash='admin-test-receipt-hash')
        Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            subject='Published',
            body_text='Event published',
            dedupe_key='failed-dashboard',
            status=Notification.Status.FAILED,
        )

        request = RequestFactory().get('/admin/', {'timeframe': 'this_month', 'kind': 'all'})
        request.user = self.staff
        context = build_dashboard_context(request)
        cards = {card['label']: card['value'] for card in context['kpi_cards']}

        self.assertEqual(cards['Organizers'], 1)
        self.assertEqual(cards['Confirmed votes'], 8)
        self.assertEqual(cards['Gross vote revenue'], Decimal('20.00'))
        self.assertEqual(cards['Ballots cast'], 1)
        self.assertEqual(cards['Turnout'], '100.0%')
        self.assertEqual(cards['Notification failures'], 1)

    def test_csv_export_includes_model_fields_and_json(self):
        event = self.create_competition(title='Export Awards')
        event.description = 'CSV export source'
        event.save(update_fields=['description'])
        modeladmin = admin.site._registry[Event]
        request = RequestFactory().post('/admin/events/event/')
        request.user = self.staff

        response = export_selected_as_csv(modeladmin, request, Event.objects.filter(pk=event.pk))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('title', content)
        self.assertIn('owner_id', content)
        self.assertIn('Export Awards', content)

    def test_changelist_export_button_exports_current_filter(self):
        included = self.create_competition(title='Visible Export Awards')
        self.create_competition(title='Hidden Export Awards')
        self.client.force_login(self.staff)

        changelist = self.client.get(
            reverse('admin:events_event_changelist'),
            {'q': included.title},
        )
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, 'Export CSV')
        self.assertContains(changelist, 'export/?q=Visible+Export+Awards')

        response = self.client.get(
            reverse('admin:events_event_export'),
            {'q': included.title},
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('Visible Export Awards', content)
        self.assertNotIn('Hidden Export Awards', content)

    def test_csv_export_denies_non_staff_direct_call(self):
        event = self.create_competition()
        modeladmin = admin.site._registry[Event]
        request = RequestFactory().post('/admin/events/event/')
        request.user = self.organizer

        with self.assertRaises(PermissionDenied):
            export_selected_as_csv(modeladmin, request, Event.objects.filter(pk=event.pk))

    def test_withdrawal_admin_action_approves_with_validation_side_effects(self):
        self.create_paid_purchase(amount=Decimal('20.00'), quantity=8)
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=get_organizer_account(self.organizer),
            amount=Decimal('5.00'),
            currency='GHS',
            payout_type=WithdrawalRequest.PayoutType.MOBILE_MONEY,
            payout_name='Ama Mensah',
            bank_name='MTN Mobile Money',
            bank_code='mtn',
            bank_account_number='0240000000',
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('admin:wallets_withdrawalrequest_changelist'),
            {
                'action': 'approve_withdrawals',
                '_selected_action': [str(withdrawal.pk)],
                'index': '0',
            },
            follow=True,
        )

        withdrawal.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(withdrawal.status, WithdrawalRequest.Status.APPROVED)
        self.assertEqual(withdrawal.reviewed_by, self.staff)
        self.assertIsNotNone(withdrawal.reviewed_at)

    def test_notification_retry_admin_action_queues_failed_notifications(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            subject='Published',
            body_text='Event published',
            dedupe_key='retry-admin',
            status=Notification.Status.FAILED,
        )
        self.client.force_login(self.staff)

        with patch('notifications.admin.dispatch_notification') as mocked_dispatch:
            response = self.client.post(
                reverse('admin:notifications_notification_changelist'),
                {
                    'action': 'retry_failed_notifications',
                    '_selected_action': [str(notification.pk)],
                    'index': '0',
                },
                follow=True,
            )

        notification.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notification.status, Notification.Status.QUEUED)
        mocked_dispatch.assert_called_once_with(notification.pk)

    def test_key_changelists_support_search_sort_and_filters(self):
        event, nominee, attempt = self.create_paid_purchase()
        InAppNotification.objects.create(
            user=self.organizer,
            title='Payment received',
            message='A payment was confirmed.',
            payment_attempt=attempt,
        )
        self.client.force_login(self.staff)

        checks = [
            (reverse('admin:events_event_changelist'), {'q': event.title, 'kind__exact': Event.Kind.PAID_COMPETITION, 'o': '1'}),
            (reverse('admin:payments_paymentattempt_changelist'), {'q': attempt.gateway_reference, 'status__exact': PaymentAttempt.Status.PAID, 'o': '1'}),
            (reverse('admin:votes_votepurchase_changelist'), {'q': nominee.name, 'currency__exact': 'GHS', 'o': '1'}),
            (reverse('admin:notifications_inappnotification_changelist'), {'q': 'Payment received', 'is_read__exact': '0', 'o': '1'}),
        ]
        for url, query in checks:
            with self.subTest(url=url):
                response = self.client.get(url, query)
                self.assertEqual(response.status_code, 200)


class PublicUrlTests(TestCase):
    @override_settings(DEBUG=True, PUBLIC_APP_URL='')
    def test_build_public_url_returns_relative_path_in_debug_without_public_base(self):
        self.assertEqual(build_public_url('/events/demo-campus-star/'), '/events/demo-campus-star/')

    @override_settings(DEBUG=False, PUBLIC_APP_URL='https://vote.vootely.com/')
    def test_build_public_url_normalizes_trailing_slash(self):
        self.assertEqual(
            build_public_url('/events/demo-campus-star/'),
            'https://vote.vootely.com/events/demo-campus-star/',
        )

    @override_settings(DEBUG=False, PUBLIC_APP_URL='')
    def test_build_public_url_requires_public_app_url_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            build_public_url('/events/demo-campus-star/')

    @override_settings(DEBUG=False, PUBLIC_APP_URL='')
    def test_deploy_check_requires_public_app_url(self):
        checks = run_checks(include_deployment_checks=True)

        self.assertTrue(any(check.id == 'votecentral.E001' for check in checks))
