import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from nominees.models import Nominee
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.models import LedgerTransaction


@override_settings(
    PAYSTACK_SECRET_KEY='sk_test_123',
    PAYSTACK_WEBHOOK_SECRET='whsec_123',
    PLATFORM_COMMISSION_RATE='0.10',
)
class PaystackPaymentTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )

    def create_event(self, **overrides):
        now = timezone.now()
        data = {
            'owner': self.organizer,
            'title': 'Best Artist Award',
            'description': 'Paystack test event',
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

    def sign_payload(self, payload):
        raw = json.dumps(payload).encode('utf-8')
        signature = hmac.new(
            settings.PAYSTACK_WEBHOOK_SECRET.encode('utf-8'),
            raw,
            hashlib.sha512,
        ).hexdigest()
        return raw, signature

    def build_payload(self, attempt):
        return {
            'event': 'charge.success',
            'data': {
                'reference': attempt.gateway_reference,
                'amount': int(attempt.amount * Decimal('100')),
                'currency': attempt.currency,
                'customer': {
                    'email': attempt.voter_email,
                    'phone': attempt.voter_phone,
                },
                'metadata': {
                    'voter_name': attempt.voter_name,
                },
            },
        }

    @patch('payments.views.initialize_paystack_transaction')
    def test_payment_initiation_creates_pending_attempt(self, mocked_initialize):
        event = self.create_event()
        nominee = self.create_nominee(event)
        mocked_initialize.return_value = {
            'status': True,
            'data': {
                'access_code': 'access-123',
                'authorization_url': 'https://paystack.test/checkout/access-123',
            },
        }

        response = self.client.post(
            reverse('payments:paystack_initiate'),
            data={
                'event_slug': event.slug,
                'nominee_ref': nominee.slug,
                'quantity': 3,
                'voter_name': 'Guest Buyer',
                'voter_email': 'buyer@example.com',
                'voter_phone': '0240000000',
            },
        )

        attempt = PaymentAttempt.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://paystack.test/checkout/access-123')
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(attempt.amount, Decimal('7.50'))
        self.assertEqual(attempt.vote_quantity, 3)
        self.assertEqual(attempt.voter_email, 'buyer@example.com')

    @patch('payments.views.initialize_paystack_transaction')
    def test_payment_initiation_returns_json_for_ajax_request(self, mocked_initialize):
        event = self.create_event()
        nominee = self.create_nominee(event)
        mocked_initialize.return_value = {
            'status': True,
            'data': {
                'access_code': 'access-ajax-123',
                'authorization_url': 'https://paystack.test/checkout/access-ajax-123',
            },
        }

        response = self.client.post(
            reverse('payments:paystack_initiate'),
            data={
                'event_slug': event.slug,
                'nominee_ref': nominee.slug,
                'quantity': 3,
                'voter_name': 'Ajax Buyer',
                'voter_email': 'ajax@example.com',
                'voter_phone': '0240000000',
            },
            headers={'Accept': 'application/json'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['access_code'], 'access-ajax-123')
        self.assertEqual(data['amount'], 7.50)
        self.assertEqual(data['quantity'], 3)

    def test_valid_webhook_marks_payment_paid_and_creates_vote_purchase(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('7.50'),
            currency='GHS',
            vote_quantity=3,
            voter_name='Guest Buyer',
            voter_email='buyer@example.com',
            voter_phone='0240000000',
            gateway_reference='webhook-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = self.build_payload(attempt)
        raw, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse('payments:paystack_webhook'),
            data=raw,
            content_type='application/json',
            headers={'x-paystack-signature': signature},
        )

        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PAID)
        self.assertTrue(VotePurchase.objects.filter(payment_attempt=attempt).exists())
        self.assertTrue(hasattr(attempt, 'ledger_transaction'))
        self.assertTrue(attempt.ledger_transaction.is_balanced)

    def test_callback_redirects_to_nominee_page_with_reference(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('7.50'),
            currency='GHS',
            vote_quantity=3,
            voter_email='buyer@example.com',
            gateway_reference='callback-ref',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(
            reverse('payments:paystack_callback'),
            data={'reference': attempt.gateway_reference, 'status': 'cancelled'},
        )

        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'{nominee.get_absolute_url()}?payment_reference={attempt.gateway_reference}',
        )
        self.assertIsNotNone(attempt.callback_received_at)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(attempt.gateway_status, 'cancelled')

    def test_callback_without_reference_falls_back_safely(self):
        response = self.client.get(reverse('payments:paystack_callback'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('payments:status_lookup'))

    def test_callback_unknown_reference_redirects_to_public_status_page(self):
        response = self.client.get(
            reverse('payments:paystack_callback'),
            data={'reference': 'missing-ref'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('payments:status_detail', args=['missing-ref']),
        )

    def test_callback_falls_back_to_public_status_page_when_event_is_not_public(self):
        event = self.create_event(status=Event.Status.DRAFT, is_public=False, published_at=None)
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('7.50'),
            currency='GHS',
            vote_quantity=3,
            voter_email='buyer@example.com',
            gateway_reference='hidden-ref',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(
            reverse('payments:paystack_callback'),
            data={'reference': attempt.gateway_reference},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('payments:status_detail', args=[attempt.gateway_reference]),
        )

    def test_nominee_page_shows_pending_payment_state(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='pending@example.com',
            gateway_reference='pending-ref',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(
            reverse('events:nominee_detail', args=[event.slug, nominee.slug]),
            data={'payment_reference': attempt.gateway_reference},
        )

        self.assertContains(response, 'Pending confirmation')
        self.assertContains(response, 'votes will count after confirmation arrives')

    def test_nominee_page_shows_paid_state_after_webhook(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='paid@example.com',
            gateway_reference='paid-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = self.build_payload(attempt)
        raw, signature = self.sign_payload(payload)
        self.client.post(
            reverse('payments:paystack_webhook'),
            data=raw,
            content_type='application/json',
            headers={'x-paystack-signature': signature},
        )

        response = self.client.get(
            reverse('events:nominee_detail', args=[event.slug, nominee.slug]),
            data={'payment_reference': attempt.gateway_reference},
        )

        self.assertContains(response, 'Vote counted')
        self.assertContains(response, 'votes have been added')

    def test_nominee_page_shows_unsuccessful_state_for_failed_payment(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='cancelled@example.com',
            gateway_reference='cancel-ref',
            status=PaymentAttempt.Status.FAILED,
            failure_reason='The payment was not completed successfully.',
        )

        response = self.client.get(
            reverse('events:nominee_detail', args=[event.slug, nominee.slug]),
            data={'payment_reference': attempt.gateway_reference},
        )

        self.assertContains(response, 'Payment unsuccessful')
        self.assertContains(response, 'no votes were added')

    def test_public_payment_status_page_shows_details_for_public_event_reference(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='pending@example.com',
            gateway_reference='status-ref-public',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(reverse('payments:status_detail', args=[attempt.gateway_reference]))

        self.assertContains(response, 'Payment submitted')
        self.assertContains(response, event.title)
        self.assertContains(response, nominee.name)
        self.assertContains(response, nominee.get_absolute_url())

    def test_public_payment_status_page_masks_hidden_event_reference(self):
        event = self.create_event(status=Event.Status.DRAFT, is_public=False, published_at=None)
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='pending@example.com',
            gateway_reference='status-ref',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(reverse('payments:status_detail', args=[attempt.gateway_reference]))

        self.assertContains(response, 'Payment submitted')
        self.assertContains(response, attempt.gateway_reference)
        self.assertNotContains(response, event.title)
        self.assertNotContains(response, nominee.name)
        self.assertNotContains(response, nominee.get_absolute_url())

    def test_public_payment_status_page_shows_not_found_for_unknown_reference(self):
        response = self.client.get(reverse('payments:status_detail', args=['unknown-ref']))

        self.assertContains(response, 'Payment reference not found')
        self.assertContains(response, 'could not find a VoteCentral payment')

    def test_payment_status_lookup_does_not_expose_other_nominee_payment(self):
        event = self.create_event()
        nominee_a = self.create_nominee(event, name='Ada')
        nominee_b = self.create_nominee(event, name='Kojo')
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee_a,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_email='secret@example.com',
            gateway_reference='secret-ref',
            status=PaymentAttempt.Status.PENDING,
        )

        response = self.client.get(
            reverse('events:nominee_payment_status', args=[event.slug, nominee_b.slug]),
            data={'payment_reference': attempt.gateway_reference},
        )

        self.assertContains(response, 'Payment reference not found')

    def test_duplicate_webhook_is_idempotent(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_name='Repeat Buyer',
            voter_email='repeat@example.com',
            gateway_reference='dup-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = self.build_payload(attempt)
        raw, signature = self.sign_payload(payload)

        for _ in range(2):
            response = self.client.post(
                reverse('payments:paystack_webhook'),
                data=raw,
                content_type='application/json',
                headers={'x-paystack-signature': signature},
            )
            self.assertEqual(response.status_code, 200)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.PAID)
        self.assertEqual(VotePurchase.objects.filter(payment_attempt=attempt).count(), 1)
        self.assertEqual(LedgerTransaction.objects.filter(payment_attempt=attempt).count(), 1)

    @patch('payments.views.initialize_paystack_transaction')
    def test_expired_event_rejects_payment_initiation(self, mocked_initialize):
        event = self.create_event(
            start_at=timezone.now() - timedelta(days=2),
            end_at=timezone.now() - timedelta(days=1),
        )
        nominee = self.create_nominee(event)

        response = self.client.post(
            reverse('payments:paystack_initiate'),
            data={
                'event_slug': event.slug,
                'nominee_ref': nominee.slug,
                'quantity': 1,
                'voter_name': 'Late Buyer',
                'voter_email': 'late@example.com',
                'voter_phone': '0240000000',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentAttempt.objects.count(), 0)
        mocked_initialize.assert_not_called()

    def test_expired_event_rejects_webhook_vote_posting(self):
        event = self.create_event(
            start_at=timezone.now() - timedelta(days=2),
            end_at=timezone.now() - timedelta(days=1),
        )
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_name='Late Buyer',
            voter_email='late@example.com',
            gateway_reference='late-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = self.build_payload(attempt)
        raw, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse('payments:paystack_webhook'),
            data=raw,
            content_type='application/json',
            headers={'x-paystack-signature': signature},
        )

        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        self.assertFalse(VotePurchase.objects.filter(payment_attempt=attempt).exists())

    def test_failed_webhook_marks_payment_as_failed(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_name='Failed Buyer',
            voter_email='failed@example.com',
            gateway_reference='failed-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = {
            'event': 'charge.failed',
            'data': {
                'reference': attempt.gateway_reference,
                'amount': 500,
                'currency': 'GHS',
                'status': 'failed',
                'gateway_response': 'Declined',
            },
        }
        raw, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse('payments:paystack_webhook'),
            data=raw,
            content_type='application/json',
            headers={'x-paystack-signature': signature},
        )

        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        self.assertEqual(attempt.failure_reason, 'Declined')
        self.assertFalse(VotePurchase.objects.filter(payment_attempt=attempt).exists())

    def test_invalid_signature_is_rejected(self):
        event = self.create_event()
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            vote_quantity=2,
            voter_name='Invalid Buyer',
            voter_email='invalid@example.com',
            gateway_reference='invalid-ref',
            status=PaymentAttempt.Status.PENDING,
        )
        payload = self.build_payload(attempt)
        raw = json.dumps(payload).encode('utf-8')

        response = self.client.post(
            reverse('payments:paystack_webhook'),
            data=raw,
            content_type='application/json',
            headers={'x-paystack-signature': 'bad-signature'},
        )

        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertFalse(VotePurchase.objects.filter(payment_attempt=attempt).exists())

    @patch('payments.views.initialize_paystack_transaction')
    def test_initialization_failure_populates_structured_failure_fields(self, mocked_initialize):
        event = self.create_event()
        nominee = self.create_nominee(event)
        mocked_initialize.side_effect = RuntimeError('Paystack temporary error')

        response = self.client.post(
            reverse('payments:paystack_initiate'),
            data={
                'event_slug': event.slug,
                'nominee_ref': nominee.slug,
                'quantity': 2,
                'voter_name': 'Buyer',
                'voter_email': 'buyer@example.com',
                'voter_phone': '0240000000',
            },
        )

        attempt = PaymentAttempt.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], nominee.get_absolute_url())
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        self.assertEqual(attempt.gateway_status, 'initialize_failed')
        self.assertEqual(attempt.failure_reason, 'Paystack temporary error')
        self.assertIsNotNone(attempt.completed_at)
