import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from nominees.models import Nominee
from notifications.adapters import NotificationSendResult
from notifications.models import Notification
from notifications.services import dispatch_notification, queue_event_reminders, queue_notification
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
        return Nominee.objects.create(event=event, name=name, is_active=True)

    def create_paid_attempt(self, event, nominee, reference='paid-ref', amount=Decimal('5.00'), quantity=2):
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency='GHS',
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

    @override_settings(
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
    )
    def test_withdrawal_submission_creates_organizer_and_staff_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('20.00'),
            currency='GHS',
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
        with patch('notifications.adapters.requests.post') as mocked_sms:
            mocked_sms.return_value.status_code = 201
            mocked_sms.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'withdrawal-sms-1'},
            }
            response = self.client.post(
                reverse('dashboard:withdrawals'),
                data={
                    'amount': '10.00',
                    'payout_name': 'Ada Organizer',
                    'bank_name': 'GCB',
                    'bank_account_number': '1234567890',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
    )
    def test_admin_withdrawal_status_transitions_create_matching_notifications(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('20.00'),
            currency='GHS',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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

        with patch('notifications.tasks.send_notification.delay', side_effect=RuntimeError('Broker down')):
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
            provider='hubtel',
        )

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'hubtel-123'},
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, 'hubtel')
        self.assertEqual(notification.provider_message_id, 'hubtel-123')
        self.assertEqual(notification.provider_status, 'accepted')

    @override_settings(
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
            provider='hubtel',
        )

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 400
            mocked_post.return_value.json.return_value = {
                'responseCode': '4001',
                'message': 'Invalid sender',
            }
            send_notification(notification.pk)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.provider_error_code, '4001')
        self.assertIn('Invalid sender', notification.failure_reason)

    @override_settings(
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
    )
    def test_queue_notification_keeps_email_and_sms_dedupe_separate(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = self.create_paid_attempt(event, nominee, reference='mixed-channels')

        with patch('notifications.adapters.requests.post') as mocked_post:
            mocked_post.return_value.status_code = 201
            mocked_post.return_value.json.return_value = {
                'responseCode': '0000',
                'data': {'status': 'accepted', 'messageId': 'mixed-1'},
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
        SMS_PROVIDER='hubtel',
        HUBTEL_CLIENT_ID='client-id',
        HUBTEL_CLIENT_SECRET='client-secret',
        HUBTEL_SMS_FROM='VoteCentral',
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
