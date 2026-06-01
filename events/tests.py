from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.views import build_event_leaderboard
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
