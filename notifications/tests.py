import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from nominees.models import CompetitionCategory, NominationSubmission, Nominee
from notifications.adapters import NotificationSendResult
from notifications.models import Notification
from notifications.services import dispatch_notification, queue_event_reminders, queue_nomination_approved, queue_nomination_rejected, queue_nomination_submitted, queue_notification, queue_sms_notification
from notifications.tasks import send_notification
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.models import WithdrawalRequest
from wallets.services import get_organizer_account, post_payment_ledger_transaction


@override_settings(
    PAYSTACK_SECRET_KEY='sk_test_123',
    PAYSTACK_WEBHOOK_SECRET='whsec_123',
    PLATFORM_COMMISSION_RATE='0.10',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='VoteCentral <no-reply@example.com>',
    SERVER_EMAIL='support@example.com',
    NOTIFICATION_ADMIN_EMAILS=['ops@example.com'],
    CELERY_TASK_ALWAYS_EAGER=True,
    SMS_PROVIDER='',
    ARKESEL_API_KEY='',
    ARKESEL_SMS_FROM='',
    EMAIL_PROVIDER='',
    BREVO_API_KEY='',
)
class NotificationFlowTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
            phone_number='+233241000111',
            sms_opt_in=True,
        )
        self.superuser = self.user_model.objects.create_superuser(
            email='admin@example.com',
            password='strong-pass-123',
        )

    def create_event(self, **overrides):
        now = timezone.now()
        data = {
            'owner': self.organizer,
            'title': 'Best Artist Award',
            'description': 'Notification test event',
            'currency': 'GHS',
            'platform_commission_percent': Decimal('10.00'),
            'vote_price': Decimal('2.50'),
            'start_at': now - timedelta(hours=1),
            'end_at': now + timedelta(days=1),
            'status': Event.Status.PUBLISHED,
            'is_public': True,
            'published_at': now - timedelta(hours=1),
        }
        data.update(overrides)
        return Event.objects.create(**data)

    def create_nominee(self, event, name='Ada'):
        category, _ = CompetitionCategory.objects.get_or_create(event=event, name=f'{name} Category')
        return Nominee.objects.create(event=event, category=category, name=name, is_active=True)

    def create_submission(self, event, status=NominationSubmission.Status.PENDING, **overrides):
        category = overrides.pop('category', None)
        if category is None:
            category, _ = CompetitionCategory.objects.get_or_create(event=event, name='Best Student')
        data = {
            'event': event,
            'category': category,
            'name': 'Ama',
            'email': 'ama@example.com',
            'phone_number': '0240000000' if status == NominationSubmission.Status.PENDING else '0240000001',
            'status': status,
        }
        data.update(overrides)
        return NominationSubmission.objects.create(**data)

    def create_paid_attempt(self, event, nominee, reference='paid-ref', amount=Decimal('5.00'), quantity=2):
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=quantity,
            voter_name='Guest Buyer',
            voter_email='buyer@example.com',
            voter_phone='0240000000',
            gateway_reference=reference,
            status=PaymentAttempt.Status.PENDING,
        )
        return attempt

    def sign_payload(self, payload):
        raw = json.dumps(payload).encode('utf-8')
        signature = hmac.new(
            settings.PAYSTACK_WEBHOOK_SECRET.encode('utf-8'),
            raw,
            hashlib.sha512,
        ).hexdigest()
        return raw, signature

    def build_success_payload(self, attempt):
        return {
            'event': 'charge.success',
            'data': {
                'reference': attempt.gateway_reference,
                'amount': int(attempt.amount * Decimal('100')),
                'currency': attempt.currency,
                'status': 'success',
                'customer': {
                    'email': attempt.voter_email,
                    'phone': attempt.voter_phone,
                },
                'metadata': {
                    'voter_name': attempt.voter_name,
                },
            },
        }

    def test_queue_nomination_submitted_notifies_organizer(self):
        event = self.create_event()
        submission = self.create_submission(event)

        queue_nomination_submitted(submission)

        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.NOMINATION_SUBMITTED,
                recipient_email=self.organizer.email,
            ).exists()
        )

    def test_queue_nomination_review_outcomes_notify_submitter(self):
        event = self.create_event()
        category = CompetitionCategory.objects.create(event=event, name='Most Fashionable')
        nominee = Nominee.objects.create(event=event, category=category, name='Esi', is_active=True)
        approved = self.create_submission(
            event,
            status=NominationSubmission.Status.APPROVED,
            category=category,
            approved_nominee=nominee,
            name='Esi',
            email='esi@example.com',
        )
        rejected = self.create_submission(
            event,
            status=NominationSubmission.Status.REJECTED,
            category=category,
            name='Kojo',
            email='kojo@example.com',
            phone_number='0240000002',
        )

        queue_nomination_approved(approved)
        queue_nomination_rejected(rejected)

        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.NOMINATION_APPROVED,
                recipient_email='esi@example.com',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.NOMINATION_REJECTED,
                recipient_email='kojo@example.com',
            ).exists()
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_successful_payment_webhook_creates_notification_row(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='notify-success')
        payload = self.build_success_payload(attempt)
        raw, signature = self.sign_payload(payload)

        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'sms-success-1'},
            }
            response = self.client.post(
                reverse('payments:paystack_webhook'),
                data=raw,
                content_type='application/json',
                headers={'x-paystack-signature': signature},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
                channel=Notification.Channel.SMS,
            ).exists()
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_failed_and_cancelled_payment_webhooks_create_matching_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        failed_attempt = self.create_paid_attempt(event, nominee, reference='notify-failed')
        cancelled_attempt = self.create_paid_attempt(event, nominee, reference='notify-cancelled')

        failure_payload = {
            'event': 'charge.failed',
            'data': {
                'reference': failed_attempt.gateway_reference,
                'amount': 500,
                'currency': 'GHS',
                'status': 'failed',
                'gateway_response': 'Declined',
            },
        }
        cancel_payload = {
            'event': 'charge.cancelled',
            'data': {
                'reference': cancelled_attempt.gateway_reference,
                'amount': 500,
                'currency': 'GHS',
                'status': 'cancelled',
                'gateway_response': 'Cancelled by user',
            },
        }

        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'sms-status-1'},
            }
            for payload in (failure_payload, cancel_payload):
                raw, signature = self.sign_payload(payload)
                self.client.post(
                    reverse('payments:paystack_webhook'),
                    data=raw,
                    content_type='application/json',
                    headers={'x-paystack-signature': signature},
                )

        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_FAILED,
                payment_attempt=failed_attempt,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CANCELLED,
                payment_attempt=cancelled_attempt,
            ).exists()
        )

    def test_browser_callback_does_not_create_notification_rows(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='callback-only')

        response = self.client.get(
            reverse('payments:paystack_callback'),
            data={'reference': attempt.gateway_reference, 'status': 'cancelled'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.count(), 0)

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_duplicate_successful_webhooks_do_not_duplicate_payment_confirmed_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='dup-success')
        payload = self.build_success_payload(attempt)
        raw, signature = self.sign_payload(payload)

        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'sms-dup-1'},
            }
            for _ in range(2):
                self.client.post(
                    reverse('payments:paystack_webhook'),
                    data=raw,
                    content_type='application/json',
                    headers={'x-paystack-signature': signature},
                )

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
                channel=Notification.Channel.EMAIL,
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
                channel=Notification.Channel.SMS,
            ).count(),
            1,
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_withdrawal_submission_creates_organizer_and_staff_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('20.00'),
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=8,
            voter_email='earned@example.com',
            gateway_reference='earned-ref',
            status=PaymentAttempt.Status.PAID,
            completed_at=timezone.now(),
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee,
            payment_attempt=attempt,
            quantity=8,
            amount_paid=Decimal('20.00'),
            currency='GHS',
            payment_reference='earned-ref',
            paid_at=timezone.now(),
        )
        post_payment_ledger_transaction(attempt)

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        cache.set(f'withdrawal_otp_{self.organizer.pk}', '654321', 600)
        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'withdrawal-sms-1'},
            }
            with patch('payments.services.get_paystack_banks', return_value=[{'code': 'MTN', 'name': 'MTN Mobile Money'}]):
                response = self.client.post(
                    reverse('dashboard:withdrawals'),
                    data={
                        'amount': '10.00',
                        'payout_type': 'mobile_money',
                        'bank_code': 'MTN',
                        'payout_name': 'Ada Organizer',
                        'bank_account_number': '0241234567',
                        'otp': '654321',
                    },
                    follow=True,
                )

        withdrawal = WithdrawalRequest.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.WITHDRAWAL_REQUESTED,
                withdrawal_request=withdrawal,
                recipient_email=self.organizer.email,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.WITHDRAWAL_REQUESTED,
                withdrawal_request=withdrawal,
                recipient_phone=self.organizer.phone_number,
                channel=Notification.Channel.SMS,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.WITHDRAWAL_REVIEW_REQUIRED,
                withdrawal_request=withdrawal,
                recipient_email='ops@example.com',
            ).exists()
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_admin_withdrawal_status_transitions_create_matching_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('20.00'),
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=8,
            voter_email='earned@example.com',
            gateway_reference='status-earned-ref',
            status=PaymentAttempt.Status.PAID,
            completed_at=timezone.now(),
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee,
            payment_attempt=attempt,
            quantity=8,
            amount_paid=Decimal('20.00'),
            currency='GHS',
            payment_reference='status-earned-ref',
            paid_at=timezone.now(),
        )
        post_payment_ledger_transaction(attempt)
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=get_organizer_account(self.organizer),
            amount=Decimal('5.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
        )
        self.client.force_login(self.superuser)

        for status, event_type in [
            (WithdrawalRequest.Status.APPROVED, Notification.EventType.WITHDRAWAL_APPROVED),
            (WithdrawalRequest.Status.PROCESSING, Notification.EventType.WITHDRAWAL_PROCESSING),
            (WithdrawalRequest.Status.REJECTED, Notification.EventType.WITHDRAWAL_REJECTED),
        ]:
            with patch('notifications.adapters.requests.post') as mocked_sms:
                mocked_sms.return_value.status_code = 201
                mocked_sms.return_value.json.return_value = {
                    'responseCode': '0000',
                    'data': {'status': 'accepted', 'messageId': f'withdrawal-{status}'},
                }
                response = self.client.post(
                    reverse('admin:wallets_withdrawalrequest_change', args=[withdrawal.pk]),
                    data={
                        'organizer': self.organizer.pk,
                        'wallet_account': withdrawal.wallet_account.pk,
                        'amount': '5.00',
                        'currency': 'GHS',
                        'payout_name': 'Ada Organizer',
                        'bank_name': 'GCB',
                        'bank_account_number': '1234567890',
                        'status': status,
                        'review_notes': f'{status} note',
                        'payout_reference': 'payout-123',
                        '_save': 'Save',
                    },
                )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                Notification.objects.filter(
                    event_type=event_type,
                    withdrawal_request=withdrawal,
                ).exists()
            )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_event_publish_and_close_create_notifications(self):
        now = timezone.now()
        event = self.create_event(
            status=Event.Status.DRAFT,
            published_at=None,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(days=2),
        )
        self.create_nominee(event)
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'event-sms-1'},
            }
            publish_response = self.client.post(
                reverse('dashboard:event_action', args=[event.slug, 'publish'])
            )
            event.refresh_from_db()
            close_response = self.client.post(
                reverse('dashboard:event_action', args=[event.slug, 'close'])
            )

        self.assertEqual(publish_response.status_code, 302)
        self.assertEqual(close_response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_PUBLISHED,
                event=event,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_CLOSED,
                event=event,
            ).exists()
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_event_reminder_scan_creates_only_one_notification_per_window(self):
        now = timezone.now()
        starting_event = self.create_event(
            title='Starting Soon',
            start_at=now + timedelta(hours=12),
            end_at=now + timedelta(days=2),
        )
        ending_event = self.create_event(
            title='Ending Soon',
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(hours=12),
        )

        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'reminder-sms-1'},
            }
            queue_event_reminders(reference_time=now)
            queue_event_reminders(reference_time=now)

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_STARTING_SOON,
                event=starting_event,
                channel=Notification.Channel.EMAIL,
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_STARTING_SOON,
                event=starting_event,
                channel=Notification.Channel.SMS,
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_ENDING_SOON,
                event=ending_event,
                channel=Notification.Channel.EMAIL,
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_ENDING_SOON,
                event=ending_event,
                channel=Notification.Channel.SMS,
            ).count(),
            1,
        )

    def test_rendered_notification_contains_expected_context(self):
        event = self.create_event(title='Campus Awards')
        nominee = self.create_nominee(event, name='Esi')
        attempt = self.create_paid_attempt(event, nominee, reference='context-ref')

        notification = queue_notification(
            event_type=Notification.EventType.PAYMENT_CONFIRMED,
            recipient_email='buyer@example.com',
            recipient_name='Guest Buyer',
            event=event,
            payment_attempt=attempt,
            dedupe_parts=(attempt.pk,),
        )

        self.assertIn('Campus Awards', notification.body_text)
        self.assertIn('Esi', notification.body_text)
        self.assertIn('context-ref', notification.body_text)

    def test_send_notification_marks_row_sent_on_success(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='send-success',
        )

        send_notification(notification.pk)
        notification.refresh_from_db()

        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(notification.provider, 'django-email')

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_queue_sms_notification_auto_detects_arkesel_provider(self):
        with patch('notifications.services.dispatch_notification') as mocked_dispatch:
            notification = queue_sms_notification(
                event_type=Notification.EventType.EVENT_PUBLISHED,
                recipient_phone='+233241234567',
                recipient_name='Owner',
                dedupe_parts=('auto-arkesel',),
            )

        self.assertIsNotNone(notification)
        self.assertEqual(notification.channel, Notification.Channel.SMS)
        self.assertEqual(notification.provider, 'arkesel')
        mocked_dispatch.assert_called_once_with(notification.pk)

    def test_queue_notification_defaults_to_django_email_without_brevo_key(self):
        with patch('notifications.services.dispatch_notification') as mocked_dispatch:
            notification = queue_notification(
                event_type=Notification.EventType.EVENT_PUBLISHED,
                recipient_email='owner@example.com',
                recipient_name='Owner',
                dedupe_parts=('email-fallback',),
            )

        self.assertIsNotNone(notification)
        self.assertEqual(notification.channel, Notification.Channel.EMAIL)
        self.assertEqual(notification.provider, 'django-email')
        mocked_dispatch.assert_called_once_with(notification.pk)

    def test_send_notification_marks_row_failed_on_error(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='send-failure',
        )

        with patch(
            'notifications.adapters.EmailAdapter.send',
            side_effect=RuntimeError('SMTP unavailable'),
        ):
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertIn('SMTP unavailable', notification.failure_reason)

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_send_notification_prefers_stored_sms_provider_over_current_settings(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='sms-provider-precedence',
            provider='arkesel',
        )

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'status': 'success',
                'data': {'messageId': 'arkesel-456'},
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, 'arkesel')
        self.assertEqual(notification.provider_message_id, 'arkesel-456')
        self.assertIn('arkesel', mocked_post.call_args.args[0])

    def test_send_notification_marks_row_failed_when_backend_reports_zero_deliveries(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='send-zero',
        )

        with patch(
            'notifications.tasks.send_notification_now',
            return_value=NotificationSendResult(deliveries=0),
        ):
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertIn('zero deliveries', notification.failure_reason)

    def test_send_notification_noops_when_row_is_already_processing(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='already-processing',
            status=Notification.Status.PROCESSING,
            attempt_count=1,
        )

        with patch('notifications.tasks.send_notification_now') as mocked_send:
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.PROCESSING)
        self.assertEqual(notification.attempt_count, 1)
        mocked_send.assert_not_called()

    def test_dispatch_notification_does_not_break_when_queue_enqueue_fails(self):
        notification = Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='dispatch-failure',
        )

        with patch('notifications.tasks.send_notification.delay', side_effect=ConnectionError('Broker down')):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertIn('Queue dispatch failed', notification.failure_reason)

    def test_notification_admin_is_readable(self):
        Notification.objects.create(
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='owner@example.com',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='admin-visible',
        )
        self.client.force_login(self.superuser)

        response = self.client.get(reverse('admin:notifications_notification_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'owner@example.com')

    def test_repeated_close_action_does_not_duplicate_event_closed_notifications(self):
        event = self.create_event(
            title='Close Once',
            status=Event.Status.PUBLISHED,
            published_at=timezone.now() - timedelta(hours=1),
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        self.client.post(reverse('dashboard:event_action', args=[event.slug, 'close']))
        self.client.post(reverse('dashboard:event_action', args=[event.slug, 'close']))

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.EVENT_CLOSED,
                event=event,
            ).count(),
            1,
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_sms_send_marks_row_sent_with_provider_metadata(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='sms-send-success',
            provider='arkesel',
        )

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'status': 'success',
                'data': [
                    {'recipient': '233241234567', 'id': 'arkesel-123'},
                ],
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, 'arkesel')
        self.assertEqual(notification.provider_message_id, 'arkesel-123')
        self.assertEqual(notification.provider_status, 'success')
        self.assertEqual(
            mocked_post.call_args.kwargs['json']['recipients'],
            ['+233241234567'],
        )


    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_sms_send_handles_list_top_level_response_from_arkesel_v2(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='arkesel-list-response-test',
            provider='arkesel',
        )

        response_obj = mock.Mock()
        response_obj.status_code = 201
        response_obj.json.return_value = [
            {'recipient': '233241234567', 'id': 'arkesel-top-list-1'},
        ]

        with patch('notifications.adapters.requests.post', return_value=response_obj):
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider_message_id, 'arkesel-top-list-1')
        self.assertEqual(notification.provider_status, 'success')

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
    )
    def test_sms_send_marks_row_failed_without_breaking_email(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Owner',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='sms-send-failure',
            provider='arkesel',
        )

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 400
            mocked_post.return_value.json.return_value = {
                'status': 'error',
                'code': 101,
                'message': 'Invalid API Key',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.provider_error_code, '101')
        self.assertIn('Invalid API Key', notification.failure_reason)

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_queue_notification_keeps_email_and_sms_dedupe_separate(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='mixed-channels')

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'status': 'success',
                'code': 1000,
                'message': 'SMS sent successfully',
                'message_id': 'mixed-1',
            }
            self.client.post(
                reverse('payments:paystack_webhook'),
                data=self.sign_payload(self.build_success_payload(attempt))[0],
                content_type='application/json',
                headers={'x-paystack-signature': self.sign_payload(self.build_success_payload(attempt))[1]},
            )

        self.assertEqual(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
            ).count(),
            2,
        )

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_sms_notifications_skip_when_phone_is_invalid(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='invalid-phone')
        attempt.voter_phone = 'bad-number'
        attempt.save(update_fields=['voter_phone'])
        payload = self.build_success_payload(attempt)
        raw, signature = self.sign_payload(payload)

        with patch('notifications.adapters.requests.post') as mocked_post:
            response = self.client.post(
                reverse('payments:paystack_webhook'),
                data=raw,
                content_type='application/json',
                headers={'x-paystack-signature': signature},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Notification.objects.filter(
                event_type=Notification.EventType.PAYMENT_CONFIRMED,
                payment_attempt=attempt,
                channel=Notification.Channel.SMS,
            ).exists()
        )
        mocked_post.assert_not_called()

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    def test_arkesel_sms_adapter_success(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Test Organizer',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='arkesel-success-test',
            provider='arkesel',
        )
        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 200
            mocked_post.return_value.json.return_value = {
                'status': 'success',
                'code': 1000,
                'message': 'SMS sent successfully',
                'message_id': 'arkesel-msg-999',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, 'arkesel')
        self.assertEqual(notification.provider_message_id, 'arkesel-msg-999')
        self.assertEqual(notification.provider_status, 'success')

    @override_settings(
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
    )
    def test_arkesel_sms_adapter_failure(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.SMS,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_phone='+233241234567',
            recipient_name='Test Organizer',
            subject='Published',
            body_text='Your event is live.',
            dedupe_key='arkesel-fail-test',
            provider='arkesel',
        )
        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 400
            mocked_post.return_value.json.return_value = {
                'status': 'error',
                'code': 101,
                'message': 'Invalid API Key',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.provider_error_code, '101')
        self.assertIn('Invalid API Key', notification.failure_reason)

    @override_settings(
        EMAIL_PROVIDER='brevo',
        BREVO_API_KEY='brevo-api-key-xyz',
        DEFAULT_FROM_EMAIL='Vootely <no-reply@vootely.com>',
    )
    def test_brevo_email_adapter_success(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.EMAIL,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='organizer@example.com',
            recipient_name='Test Organizer',
            subject='Welcome to Vootely',
            body_text='Welcome!',
            body_html='<p>Welcome!</p>',
            dedupe_key='brevo-success-test',
            provider='brevo',
        )
        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'messageId': 'brevo-msg-888',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, 'brevo')
        self.assertEqual(notification.provider_message_id, 'brevo-msg-888')

    @override_settings(
        EMAIL_PROVIDER='brevo',
        BREVO_API_KEY='brevo-api-key-xyz',
        DEFAULT_FROM_EMAIL='Vootely <no-reply@vootely.com>',
    )
    def test_brevo_email_adapter_failure(self):
        notification = Notification.objects.create(
            channel=Notification.Channel.EMAIL,
            event_type=Notification.EventType.EVENT_PUBLISHED,
            recipient_email='organizer@example.com',
            recipient_name='Test Organizer',
            subject='Welcome to Vootely',
            body_text='Welcome!',
            dedupe_key='brevo-fail-test',
            provider='brevo',
        )
        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 400
            mocked_post.return_value.json.return_value = {
                'code': 'invalid_parameter',
                'message': 'domain not configured',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.provider_error_code, 'invalid_parameter')
        self.assertIn('domain not configured', notification.failure_reason)

    @mock.patch('notifications.services.sms_channel_ready', return_value=True)
    def test_voter_turnout_reminders_queues_notifications(self, mock_sms_ready):
        from .services import queue_voter_turnout_reminders
        from elections.models import ElectionCredential, ElectionVoter
        
        # Create a secure election ending in 12 hours
        now = timezone.now()
        event = Event.objects.create(
            owner=self.organizer,
            title='SRC Election',
            description='Secure campus election',
            kind=Event.Kind.SECURE_ELECTION,
            currency='GHS',
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=12),
            is_public=True,
            status=Event.Status.OPEN,
        )

        voter = ElectionVoter.objects.create(
            event=event,
            external_id='V001',
            name='Voter 1',
            email='voter1@example.com',
            phone='0241234567',
            status=ElectionVoter.Status.ELIGIBLE,
        )

        ElectionCredential.objects.create(
            event=event,
            voter=voter,
            token_hash='some_token_hash_abc',
            status=ElectionCredential.Status.ISSUED,
        )

        # Scan for reminders
        created_ids = queue_voter_turnout_reminders(reference_time=now)

        # Assert notifications exist
        self.assertEqual(len(created_ids), 2)  # 1 Email + 1 SMS
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.VOTER_TURNOUT_REMINDER,
                recipient_email='voter1@example.com',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.VOTER_TURNOUT_REMINDER,
                recipient_phone='+233241234567',
            ).exists()
        )
