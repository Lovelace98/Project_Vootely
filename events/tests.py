from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.performance import dashboard_home_context, build_event_leaderboard
from nominees.models import Nominee
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.services import post_payment_ledger_transaction


class EventFlowTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='owner@example.com',
            password='strong-pass-123',
        )
        self.other_user = self.user_model.objects.create_user(
            email='other@example.com',
            password='strong-pass-123',
        )

    def create_event(self, owner=None, **overrides):
        now = timezone.now()
        data = {
            'owner': owner or self.organizer,
            'title': 'Campus Face of the Year',
            'description': 'Campus competition',
            'currency': 'GHS',
            'vote_price': Decimal('2.50'),
            'start_at': now - timedelta(hours=1),
            'end_at': now + timedelta(days=2),
            'status': Event.Status.DRAFT,
            'is_public': True,
        }
        data.update(overrides)
        return Event.objects.create(**data)

    def create_nominee(self, event, name='Ada'):
        return Nominee.objects.create(event=event, name=name, is_active=True)

    def test_organizer_cannot_access_another_organizers_event(self):
        event = self.create_event(owner=self.other_user)
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.get(reverse('dashboard:event_detail', args=[event.slug]))

        self.assertEqual(response.status_code, 404)

    def test_publish_action_requires_nominee_price_and_valid_dates(self):
        event = self.create_event(vote_price=None)
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(reverse('dashboard:event_action', args=[event.slug, 'publish']))

        event.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_public_pages_hide_draft_events_and_show_published_event(self):
        draft = self.create_event(title='Hidden Draft')
        published = self.create_event(title='Live Event')
        self.create_nominee(published, name='Nominee One')
        published.publish()

        home_response = self.client.get(reverse('events:home'))
        published_response = self.client.get(reverse('events:public_detail', args=[published.slug]))
        draft_response = self.client.get(reverse('events:public_detail', args=[draft.slug]))

        self.assertContains(home_response, 'Live Event')
        self.assertNotContains(home_response, 'Hidden Draft')
        self.assertEqual(published_response.status_code, 200)
        self.assertEqual(draft_response.status_code, 404)

    def test_leaderboard_aggregation_uses_vote_purchase_quantity(self):
        event = self.create_event(status=Event.Status.PUBLISHED)
        nominee_a = self.create_nominee(event, name='Ada')
        nominee_b = self.create_nominee(event, name='Kojo')

        attempt_a = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee_a,
            amount=Decimal('10.00'),
            currency='GHS',
            vote_quantity=4,
            voter_email='a@example.com',
            gateway_reference='ref-a',
            status=PaymentAttempt.Status.PAID,
        )
        attempt_b = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee_b,
            amount=Decimal('15.00'),
            currency='GHS',
            vote_quantity=6,
            voter_email='b@example.com',
            gateway_reference='ref-b',
            status=PaymentAttempt.Status.PAID,
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee_a,
            payment_attempt=attempt_a,
            quantity=4,
            amount_paid=Decimal('10.00'),
            currency='GHS',
            payment_reference='ref-a',
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee_b,
            payment_attempt=attempt_b,
            quantity=6,
            amount_paid=Decimal('15.00'),
            currency='GHS',
            payment_reference='ref-b',
        )

        leaderboard = list(build_event_leaderboard(event))

        self.assertEqual(leaderboard[0].name, 'Kojo')
        self.assertEqual(leaderboard[0].total_votes, 6)
        self.assertEqual(leaderboard[1].total_votes, 4)

    def test_dashboard_summary_matches_vote_purchases(self):
        event = self.create_event(status=Event.Status.PUBLISHED)
        nominee = self.create_nominee(event)
        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('12.50'),
            currency='GHS',
            vote_quantity=5,
            voter_email='buyer@example.com',
            gateway_reference='summary-ref',
            status=PaymentAttempt.Status.PAID,
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee,
            payment_attempt=attempt,
            quantity=5,
            amount_paid=Decimal('12.50'),
            currency='GHS',
            payment_reference='summary-ref',
        )
        post_payment_ledger_transaction(attempt)

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.get(reverse('dashboard:home'))

        self.assertContains(response, '5')
        self.assertContains(response, '12.50')

    def test_dashboard_summary_cache_invalidates_after_vote_purchase(self):
        cache.clear()
        event = self.create_event(status=Event.Status.PUBLISHED)
        nominee = self.create_nominee(event)

        first_context = dashboard_home_context(self.organizer, timeframe='all_time')
        self.assertEqual(first_context['summary']['confirmed_votes'], 0)

        attempt = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('7.50'),
            currency='GHS',
            vote_quantity=3,
            voter_email='cached@example.com',
            gateway_reference='cache-invalidation-ref',
            status=PaymentAttempt.Status.PAID,
        )
        VotePurchase.objects.create(
            event=event,
            nominee=nominee,
            payment_attempt=attempt,
            quantity=3,
            amount_paid=Decimal('7.50'),
            currency='GHS',
            payment_reference='cache-invalidation-ref',
        )

        refreshed_context = dashboard_home_context(self.organizer, timeframe='all_time')
        self.assertEqual(refreshed_context['summary']['confirmed_votes'], 3)
        self.assertEqual(refreshed_context['summary']['confirmed_revenue'], Decimal('7.50'))

    def test_dashboard_summary_cache_is_scoped_by_organizer(self):
        cache.clear()
        own_event = self.create_event(status=Event.Status.PUBLISHED)
        own_nominee = self.create_nominee(own_event, name='Own Nominee')
        other_event = self.create_event(owner=self.other_user, status=Event.Status.PUBLISHED, title='Other Event')
        other_nominee = self.create_nominee(other_event, name='Other Nominee')

        for event, nominee, reference, amount, quantity in [
            (own_event, own_nominee, 'own-cache-scope', Decimal('5.00'), 2),
            (other_event, other_nominee, 'other-cache-scope', Decimal('20.00'), 8),
        ]:
            attempt = PaymentAttempt.objects.create(
                event=event,
                nominee=nominee,
                amount=amount,
                currency='GHS',
                vote_quantity=quantity,
                voter_email=f'{reference}@example.com',
                gateway_reference=reference,
                status=PaymentAttempt.Status.PAID,
            )
            VotePurchase.objects.create(
                event=event,
                nominee=nominee,
                payment_attempt=attempt,
                quantity=quantity,
                amount_paid=amount,
                currency='GHS',
                payment_reference=reference,
            )

        context = dashboard_home_context(self.organizer, timeframe='all_time')
        self.assertEqual(context['summary']['confirmed_votes'], 2)
        self.assertEqual(context['summary']['confirmed_revenue'], Decimal('5.00'))
