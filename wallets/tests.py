from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from nominees.models import CompetitionCategory, Nominee
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from events.performance import dashboard_analytics_context

from .models import LedgerEntry, LedgerTransaction, WithdrawalRequest
from .services import (
    get_available_withdrawal_balance,
    post_payment_ledger_transaction,
)


class RevenuePageTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='finance@example.com',
            password='strong-pass-123',
        )
        self.other = self.user_model.objects.create_user(
            email='other@example.com',
            password='strong-pass-123',
        )

    def create_event(self, owner, title):
        now = timezone.now()
        return Event.objects.create(
            owner=owner,
            title=title,
            description='Finance event',
            currency='GHS',
            platform_commission_percent=Decimal('10.00'),
            vote_price=Decimal('2.50'),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1),
            status=Event.Status.PUBLISHED,
            is_public=True,
            published_at=now - timedelta(minutes=30),
        )

    def create_nominee(self, event, name):
        category, _ = CompetitionCategory.objects.get_or_create(event=event, name=f'{name} Category')
        return Nominee.objects.create(event=event, category=category, name=name, is_active=True)

    def create_paid_attempt(self, event, nominee, reference, amount, quantity):
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=quantity,
            voter_email=f'{reference}@example.com',
            gateway_reference=reference,
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
            payment_reference=reference,
            paid_at=timezone.now(),
        )
        post_payment_ledger_transaction(attempt)
        return attempt

    def test_revenue_page_requires_authentication(self):
        response = self.client.get(reverse('dashboard:revenue'))

        self.assertEqual(response.status_code, 302)

    def test_revenue_page_is_scoped_to_organizer_data(self):
        own_event = self.create_event(self.organizer, 'Own Event')
        own_nominee = self.create_nominee(own_event, 'Ada')
        self.create_paid_attempt(own_event, own_nominee, 'own-paid', Decimal('20.00'), 8)
        PaymentAttempt.objects.create(
            event=own_event,
            nominee=own_nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            platform_commission_percent=own_event.platform_commission_percent,
            vote_quantity=2,
            voter_email='pending@example.com',
            gateway_reference='own-pending',
            status=PaymentAttempt.Status.PENDING,
        )

        other_event = self.create_event(self.other, 'Other Event')
        other_nominee = self.create_nominee(other_event, 'Kojo')
        self.create_paid_attempt(other_event, other_nominee, 'other-paid', Decimal('30.00'), 12)

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.get(reverse('dashboard:revenue'))

        self.assertContains(response, 'Own Event')
        self.assertNotContains(response, 'Other Event')
        self.assertContains(response, '20.00')
        self.assertContains(response, '18.00')
        self.assertContains(response, '2.00')
        self.assertContains(response, '5.00')

    def test_revenue_page_uses_confirmed_and_pending_separately(self):
        event = self.create_event(self.organizer, 'Campus Awards')
        nominee = self.create_nominee(event, 'Esi')
        self.create_paid_attempt(event, nominee, 'gross-paid', Decimal('12.50'), 5)
        PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('7.50'),
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=3,
            voter_email='pending@example.com',
            gateway_reference='pending-only',
            status=PaymentAttempt.Status.PENDING,
        )

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.get(reverse('dashboard:revenue'))

        organizer_credits = LedgerEntry.objects.filter(
            account__owner=self.organizer,
            kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
        )
        self.assertContains(response, '12.50')
        self.assertContains(response, '7.50')
        self.assertContains(response, str(organizer_credits.first().amount))

    def test_analytics_context_applies_timeframe_to_nominee_rows(self):
        cache.clear()
        event = self.create_event(self.organizer, 'Analytics Event')
        nominee = self.create_nominee(event, 'Esi')
        old_attempt = self.create_paid_attempt(event, nominee, 'old-analytics', Decimal('10.00'), 4)
        VotePurchase.objects.filter(payment_attempt=old_attempt).update(
            paid_at=timezone.now() - timedelta(days=45)
        )
        self.create_paid_attempt(event, nominee, 'new-analytics', Decimal('5.00'), 2)

        context = dashboard_analytics_context(self.organizer, timeframe='this_month')

        self.assertEqual(context['total_votes'], 2)
        self.assertEqual(context['total_revenue'], Decimal('5.00'))
        self.assertEqual(context['nominees_perf'][0].votes, 2)
        self.assertEqual(context['nominees_perf'][0].earnings, Decimal('5.00'))

    def test_analytics_nominees_fragment_requires_authentication(self):
        response = self.client.get(reverse('dashboard:analytics_nominees_fragment'))

        self.assertEqual(response.status_code, 302)

    def test_analytics_nominees_fragment_is_scoped_to_organizer(self):
        cache.clear()
        own_event = self.create_event(self.organizer, 'Own Analytics Event')
        own_nominee = self.create_nominee(own_event, 'Ama')
        self.create_paid_attempt(own_event, own_nominee, 'own-fragment', Decimal('12.00'), 6)

        other_event = self.create_event(self.other, 'Other Analytics Event')
        other_nominee = self.create_nominee(other_event, 'Kojo')
        self.create_paid_attempt(other_event, other_nominee, 'other-fragment', Decimal('18.00'), 9)

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.get(reverse('dashboard:analytics_nominees_fragment'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ama')
        self.assertContains(response, 'Own Analytics Event')
        self.assertContains(response, '6 votes')
        self.assertNotContains(response, 'Kojo')
        self.assertNotContains(response, 'Other Analytics Event')


class WithdrawalFlowTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='withdraw@example.com',
            password='strong-pass-123',
        )
        self.staff = self.user_model.objects.create_superuser(
            email='staff@example.com',
            password='strong-pass-123',
        )

    def create_event(self, owner=None, title='Withdrawal Event', commission_percent=Decimal('10.00')):
        now = timezone.now()
        return Event.objects.create(
            owner=owner or self.organizer,
            title=title,
            description='Finance event',
            currency='GHS',
            platform_commission_percent=commission_percent,
            vote_price=Decimal('2.50'),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1),
            status=Event.Status.PUBLISHED,
            is_public=True,
            published_at=now - timedelta(minutes=30),
        )

    def create_nominee(self, event, name='Ada'):
        category, _ = CompetitionCategory.objects.get_or_create(event=event, name=f'{name} Category')
        return Nominee.objects.create(event=event, category=category, name=name, is_active=True)

    def create_paid_attempt(self, amount=Decimal('20.00'), quantity=8, *, commission_percent=Decimal('10.00'), event=None):
        event = event or self.create_event(commission_percent=commission_percent)
        nominee = self.create_nominee(event, 'Ada')
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=amount,
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=quantity,
            voter_email='earned@example.com',
            gateway_reference=f'paid-{amount}-{quantity}',
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
        return attempt

    def withdrawal_post_data(self, amount='10.00', otp='654321', **overrides):
        data = {
            'amount': amount,
            'payout_type': 'mobile_money',
            'bank_code': 'MTN',
            'bank_account_number': '0241234567',
            'payout_name': 'Ada Organizer',
            'otp': otp,
        }
        data.update(overrides)
        return data

    def test_withdrawals_page_requires_authentication(self):
        response = self.client.get(reverse('dashboard:withdrawals'))

        self.assertEqual(response.status_code, 302)

    def test_withdrawal_request_is_scoped_to_organizer_balance(self):
        self.create_paid_attempt(amount=Decimal('20.00'))
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        cache.set(f'withdrawal_otp_{self.organizer.pk}', '654321', 600)

        with patch('payments.services.get_paystack_banks', return_value=[{'code': 'MTN', 'name': 'MTN Mobile Money'}]):
            response = self.client.post(
                reverse('dashboard:withdrawals'),
                data=self.withdrawal_post_data(),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WithdrawalRequest.objects.filter(organizer=self.organizer).count(), 1)
        self.assertContains(response, 'Withdrawal request submitted')
        self.assertFalse(cache.get(f'withdrawal_otp_{self.organizer.pk}'))

    def test_withdrawal_request_cannot_exceed_available_balance(self):
        self.create_paid_attempt(amount=Decimal('9.00'))
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        cache.set(f'withdrawal_otp_{self.organizer.pk}', '654321', 600)

        with patch('payments.services.get_paystack_banks', return_value=[{'code': 'MTN', 'name': 'MTN Mobile Money'}]):
            response = self.client.post(
                reverse('dashboard:withdrawals'),
                data=self.withdrawal_post_data(amount='20.00'),
            )

        self.assertEqual(WithdrawalRequest.objects.count(), 0)
        self.assertContains(response, 'exceeds the available balance')

    def test_withdrawal_request_requires_valid_otp(self):
        self.create_paid_attempt(amount=Decimal('9.00'))
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        with patch('payments.services.get_paystack_banks', return_value=[{'code': 'MTN', 'name': 'MTN Mobile Money'}]):
            response = self.client.post(
                reverse('dashboard:withdrawals'),
                data=self.withdrawal_post_data(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WithdrawalRequest.objects.count(), 0)
        self.assertContains(response, 'OTP has expired or was never requested')

    def test_request_otp_endpoint_rate_limits_after_five_attempts(self):
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        for _ in range(5):
            response = self.client.post(reverse('dashboard:request_otp'))
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('dashboard:request_otp'))

        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many code requests', response.content.decode())

    def test_available_balance_excludes_completed_withdrawals(self):
        self.create_paid_attempt(amount=Decimal('20.00'))
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('8.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
            status=WithdrawalRequest.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        from .services import post_withdrawal_ledger_transaction

        post_withdrawal_ledger_transaction(withdrawal)

        self.assertEqual(get_available_withdrawal_balance(self.organizer), Decimal('10.00'))

    def test_available_balance_excludes_pending_withdrawals(self):
        self.create_paid_attempt(amount=Decimal('20.00'))
        WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('8.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
            status=WithdrawalRequest.Status.PENDING,
        )
        self.assertEqual(get_available_withdrawal_balance(self.organizer), Decimal('10.00'))

    def test_admin_completion_posts_withdrawal_ledger_transaction(self):
        self.create_paid_attempt(amount=Decimal('20.00'))
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('9.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('admin:wallets_withdrawalrequest_change', args=[withdrawal.pk]),
            data={
                'organizer': self.organizer.pk,
                'wallet_account': self.organizer.wallet_account.pk,
                'amount': '9.00',
                'currency': 'GHS',
                'payout_name': 'Ada Organizer',
                'bank_name': 'GCB',
                'bank_account_number': '1234567890',
                'status': WithdrawalRequest.Status.COMPLETED,
                'review_notes': 'Approved',
                'payout_reference': 'payout-123',
                '_save': 'Save',
            },
            follow=True,
        )

        withdrawal.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(withdrawal.status, WithdrawalRequest.Status.COMPLETED)
        self.assertTrue(LedgerTransaction.objects.filter(withdrawal_request=withdrawal).exists())
        self.assertTrue(withdrawal.ledger_transaction.is_balanced)

    def test_admin_completion_rejects_over_withdrawal(self):
        self.create_paid_attempt(amount=Decimal('10.00'))
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('20.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('admin:wallets_withdrawalrequest_change', args=[withdrawal.pk]),
            data={
                'organizer': self.organizer.pk,
                'wallet_account': self.organizer.wallet_account.pk,
                'amount': '20.00',
                'currency': 'GHS',
                'payout_name': 'Ada Organizer',
                'bank_name': 'GCB',
                'bank_account_number': '1234567890',
                'status': WithdrawalRequest.Status.COMPLETED,
                'review_notes': 'Too high',
                'payout_reference': 'payout-999',
                '_save': 'Save',
            },
        )

        withdrawal.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(withdrawal.status, WithdrawalRequest.Status.PENDING)
        self.assertContains(response, 'exceeds the organizer available balance')

    def test_revenue_and_withdrawals_pages_stay_consistent_after_payout(self):
        self.create_paid_attempt(amount=Decimal('20.00'))
        withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('9.00'),
            currency='GHS',
            payout_name='Ada Organizer',
            bank_name='GCB',
            bank_account_number='1234567890',
            status=WithdrawalRequest.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        from .services import post_withdrawal_ledger_transaction

        post_withdrawal_ledger_transaction(withdrawal)
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        revenue_response = self.client.get(reverse('dashboard:revenue'))
        withdrawals_response = self.client.get(reverse('dashboard:withdrawals'))

        self.assertContains(revenue_response, 'Available to Withdraw')
        self.assertContains(revenue_response, '9.00')
        self.assertContains(withdrawals_response, 'Total Withdrawn')
        self.assertContains(withdrawals_response, '9.00')

    def test_available_balance_uses_net_earnings_across_mixed_commission_events(self):
        first_event = self.create_event(title='10 Percent Event', commission_percent=Decimal('10.00'))
        second_event = self.create_event(title='25 Percent Event', commission_percent=Decimal('25.00'))
        self.create_paid_attempt(amount=Decimal('100.00'), quantity=40, event=first_event)
        self.create_paid_attempt(amount=Decimal('80.00'), quantity=32, event=second_event)

        approved_withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('40.00'),
            currency='GHS',
            payout_type=WithdrawalRequest.PayoutType.MOBILE_MONEY,
            payout_name='Ada Organizer',
            bank_name='MTN Mobile Money',
            bank_code='MTN',
            bank_account_number='0241234567',
            status=WithdrawalRequest.Status.APPROVED,
        )
        completed_withdrawal = WithdrawalRequest.objects.create(
            organizer=self.organizer,
            wallet_account=self.organizer.wallet_account,
            amount=Decimal('10.00'),
            currency='GHS',
            payout_type=WithdrawalRequest.PayoutType.MOBILE_MONEY,
            payout_name='Ada Organizer',
            bank_name='MTN Mobile Money',
            bank_code='MTN',
            bank_account_number='0241234567',
            status=WithdrawalRequest.Status.COMPLETED,
            completed_at=timezone.now(),
        )

        from .services import post_withdrawal_ledger_transaction

        post_withdrawal_ledger_transaction(completed_withdrawal)

        self.assertEqual(get_available_withdrawal_balance(self.organizer), Decimal('100.00'))
        self.assertEqual(approved_withdrawal.amount, Decimal('40.00'))
