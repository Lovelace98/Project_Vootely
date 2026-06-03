from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import ContactInquiry, Event
from events.performance import dashboard_home_context, build_event_leaderboard
from nominees.models import CompetitionCategory, Nominee, NominationSubmission
from notifications.models import Notification
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
            'platform_commission_percent': Decimal('10.00'),
            'vote_price': Decimal('2.50'),
            'start_at': now - timedelta(hours=1),
            'end_at': now + timedelta(days=2),
            'status': Event.Status.DRAFT,
            'is_public': True,
        }
        data.update(overrides)
        return Event.objects.create(**data)

    def create_nominee(self, event, name='Ada'):
        category, _ = CompetitionCategory.objects.get_or_create(event=event, name=f'{name} Category')
        return Nominee.objects.create(event=event, category=category, name=name, is_active=True)

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

    def test_publish_allows_nomination_only_event_when_window_and_category_exist(self):
        now = timezone.now()
        event = self.create_event(
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=2),
            nomination_end_at=now + timedelta(days=1),
        )
        CompetitionCategory.objects.create(event=event, name='Best Student')

        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(reverse('dashboard:event_action', args=[event.slug, 'publish']))

        event.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.status, Event.Status.PUBLISHED)

    def test_publish_requires_platform_commission(self):
        event = self.create_event(platform_commission_percent=None)
        CompetitionCategory.objects.create(event=event, name='Best Student')
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(reverse('dashboard:event_action', args=[event.slug, 'publish']), follow=True)

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)
        self.assertContains(response, 'Platform commission must be set by Vootely admin before publish.')

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        SMS_PROVIDER='arkesel',
        ARKESEL_API_KEY='arkesel-key-123',
        ARKESEL_SMS_FROM='Vootely',
    )
    @patch('notifications.adapters.requests.post')
    def test_creating_paid_event_queues_admin_commission_alerts(self, mocked_sms):
        mocked_sms.return_value.status_code = 201
        mocked_sms.return_value.json.return_value = {
            'responseCode': '0000',
            'data': {'status': 'accepted', 'messageId': 'commission-sms-1'},
        }
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        start_at = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        end_at = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')

        response = self.client.post(
            reverse('dashboard:event_create'),
            {
                'title': 'Fresh Awards',
                'description': 'New event awaiting commission',
                'currency': 'GHS',
                'vote_price': '2.50',
                'start_at': start_at,
                'end_at': end_at,
                'is_public': 'on',
                'show_leaderboard': 'on',
            },
        )

        event = Event.objects.get(title='Fresh Awards')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(
                event=event,
                event_type=Notification.EventType.EVENT_COMMISSION_SETUP_REQUIRED,
                recipient_email='lovesdesigns1@gmail.com',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event=event,
                event_type=Notification.EventType.EVENT_COMMISSION_SETUP_REQUIRED,
                channel=Notification.Channel.SMS,
                recipient_phone='+233548988503',
            ).exists()
        )

    def test_platform_commission_locks_after_first_successful_paid_vote(self):
        event = self.create_event(status=Event.Status.PUBLISHED, published_at=timezone.now() - timedelta(hours=1))
        nominee = self.create_nominee(event)
        PaymentAttempt.objects.create(
            event=event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
            vote_quantity=2,
            voter_email='buyer@example.com',
            gateway_reference='lock-ref',
            status=PaymentAttempt.Status.PAID,
        )

        event.platform_commission_percent = Decimal('12.00')
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_root_landing_page_contains_public_ctas(self):
        self.assertEqual(reverse('events:landing'), '/')
        self.assertEqual(reverse('events:home'), '/events/')

        # Create a published event so the featured events carousel (containing 'See all events') is rendered
        event = self.create_event(status=Event.Status.PUBLISHED)
        self.create_nominee(event)

        response = self.client.get(reverse('events:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Host an Event')
        self.assertContains(response, 'Browse Events')
        self.assertContains(response, 'secure elections')
        self.assertContains(response, 'Financial Lawrence')
        self.assertContains(response, 'Jeffren Kane')
        self.assertContains(response, 'landing-hero-woman-transparent.png')
        self.assertContains(response, 'Affordable platform management fee')
        self.assertContains(response, 'negotiated platform commission')
        self.assertContains(response, 'lovesdesigns1@gmail.com')
        self.assertContains(response, 'https://wa.me/233548988503?text=')
        self.assertContains(response, 'See all events')
        self.assertContains(response, 'Browse active competitions already collecting votes.')
        self.assertContains(response, 'FAQ')
        self.assertContains(response, '#faq')
        self.assertContains(response, 'How do platform fees work for paid competitions?')
        self.assertContains(response, 'Each event has its own agreed platform commission')
        self.assertContains(response, 'Secure elections use custom election pricing')
        self.assertNotContains(response, 'Public voting, simplified')
        self.assertNotContains(response, 'Your landing page should explain the value quickly.')
        self.assertNotContains(response, 'Structured election workflows')
        self.assertNotContains(response, 'Mobile-friendly voter experience')
        self.assertNotContains(response, '10%')
        self.assertContains(response, reverse('events:home'))
        self.assertContains(response, reverse('events:landing'))
        self.assertContains(response, reverse('events:contact_inquiry_submit'))

    @override_settings(PUBLIC_APP_URL='https://vote.vootely.com')
    def test_public_event_nomination_link_uses_configured_public_app_url(self):
        now = timezone.now()
        event = self.create_event(
            status=Event.Status.PUBLISHED,
            published_at=now - timedelta(hours=1),
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        CompetitionCategory.objects.create(event=event, name='Best Student')

        response = self.client.get(reverse('events:public_detail', args=[event.slug]), HTTP_HOST='127.0.0.1:8000')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://vote.vootely.com/events/')
        self.assertNotContains(response, 'http://127.0.0.1:8000/events/')

    @override_settings(PUBLIC_APP_URL='https://vote.vootely.com')
    def test_dashboard_nomination_link_uses_configured_public_app_url(self):
        now = timezone.now()
        event = self.create_event(
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        CompetitionCategory.objects.create(event=event, name='Best Student')
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.get(reverse('dashboard:event_detail', args=[event.slug]), HTTP_HOST='husband-projector-budding.ngrok-free.dev')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://vote.vootely.com/events/')
        self.assertNotContains(response, 'https://husband-projector-budding.ngrok-free.dev/events/')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_contact_inquiry_submit_saves_record_and_sends_email(self):
        response = self.client.post(
            reverse('events:contact_inquiry_submit'),
            {
                'name': 'Ama Mensah',
                'email': 'ama@example.com',
                'phone_number': '+233240000000',
                'heard_about_us': ContactInquiry.HeardAboutUs.WHATSAPP,
                'message': 'We want to run a departmental election.',
            },
        )

        self.assertRedirects(response, f"{reverse('events:landing')}#contact")
        self.assertEqual(ContactInquiry.objects.count(), 1)
        inquiry = ContactInquiry.objects.get()
        self.assertEqual(inquiry.name, 'Ama Mensah')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Landing inquiry from Ama Mensah', mail.outbox[0].subject)
        self.assertIn('departmental election', mail.outbox[0].body)

    def test_contact_inquiry_submit_invalid_data_shows_errors(self):
        response = self.client.post(
            reverse('events:contact_inquiry_submit'),
            {
                'name': '',
                'email': 'not-an-email',
                'phone_number': '',
                'heard_about_us': '',
                'message': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactInquiry.objects.count(), 0)
        self.assertContains(response, 'This field is required.')
        self.assertContains(response, 'Enter a valid email address.')

    @patch('events.views.send_mail', side_effect=Exception('email down'))
    def test_contact_inquiry_submit_keeps_saved_inquiry_when_email_fails(self, mocked_send_mail):
        response = self.client.post(
            reverse('events:contact_inquiry_submit'),
            {
                'name': 'Kwame Doe',
                'email': 'kwame@example.com',
                'phone_number': '+233540000000',
                'heard_about_us': ContactInquiry.HeardAboutUs.GOOGLE_SEARCH,
                'message': 'Please contact us about a competition.',
            },
            follow=True,
        )

        self.assertEqual(ContactInquiry.objects.count(), 1)
        mocked_send_mail.assert_called_once()
        self.assertContains(response, 'Your message has been saved.')

    def test_public_events_route_hides_draft_events_and_show_published_event(self):
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

    def test_public_event_groups_nominees_by_category_and_shows_nomination_cta(self):
        now = timezone.now()
        event = self.create_event(
            title='Awards Night',
            status=Event.Status.PUBLISHED,
            published_at=now - timedelta(hours=1),
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        category = CompetitionCategory.objects.create(event=event, name='Most Fashionable')
        Nominee.objects.create(event=event, category=category, name='Esi', is_active=True)

        response = self.client.get(reverse('events:public_detail', args=[event.slug]))

        self.assertContains(response, 'Most Fashionable')
        self.assertContains(response, 'Submit your nomination')

    def test_public_nomination_submission_creates_pending_record(self):
        now = timezone.now()
        event = self.create_event(
            title='Awards Night',
            status=Event.Status.PUBLISHED,
            published_at=now - timedelta(hours=1),
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        category = CompetitionCategory.objects.create(event=event, name='Best Student')

        response = self.client.post(
            reverse('events:nominate', args=[event.slug]),
            data={
                'category': category.pk,
                'name': 'Ama',
                'bio': 'Student leader',
                'email': 'ama@example.com',
                'phone_number': '0240000000',
            },
            follow=True,
        )

        submission = NominationSubmission.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submission.status, NominationSubmission.Status.PENDING)
        self.assertEqual(submission.category, category)

    def test_leaderboard_aggregation_uses_vote_purchase_quantity(self):
        event = self.create_event(status=Event.Status.PUBLISHED)
        nominee_a = self.create_nominee(event, name='Ada')
        nominee_b = self.create_nominee(event, name='Kojo')

        attempt_a = PaymentAttempt.objects.create(
            event=event,
            nominee=nominee_a,
            amount=Decimal('10.00'),
            currency='GHS',
            platform_commission_percent=event.platform_commission_percent,
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
            platform_commission_percent=event.platform_commission_percent,
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
            platform_commission_percent=event.platform_commission_percent,
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
            platform_commission_percent=event.platform_commission_percent,
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
                platform_commission_percent=event.platform_commission_percent,
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

    def test_nominee_detail_page_hides_leaderboard_when_show_leaderboard_is_false(self):
        event = self.create_event(status=Event.Status.PUBLISHED, show_leaderboard=False)
        nominee = self.create_nominee(event, name='Ada')

        response = self.client.get(
            reverse('events:nominee_detail', args=[event.slug, nominee.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Leaderboard is hidden')
        self.assertIsNone(response.context.get('leaderboard'))
        self.assertTrue(response.context.get('leaderboard_hidden'))

    def test_nominee_detail_page_shows_leaderboard_when_show_leaderboard_is_true(self):
        event = self.create_event(status=Event.Status.PUBLISHED, show_leaderboard=True)
        nominee = self.create_nominee(event, name='Ada')

        response = self.client.get(
            reverse('events:nominee_detail', args=[event.slug, nominee.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Leaderboard is hidden')
        self.assertIsNotNone(response.context.get('leaderboard'))
        self.assertIsNone(response.context.get('leaderboard_hidden'))

    def test_dashboard_comparison_trends(self):
        from datetime import datetime, timezone as dt_timezone
        cache.clear()
        # Mock timezone.now to a fixed date: June 15, 2026
        fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=dt_timezone.utc)

        # 1. Previous period (May 2026) event and purchase
        may_time = fixed_now - timedelta(days=20) # May 26, 2026
        with patch('django.utils.timezone.now', return_value=may_time):
            event_may = self.create_event(status=Event.Status.PUBLISHED, published_at=may_time)
            nominee_may = self.create_nominee(event_may, name='Ada May')
            attempt_may = PaymentAttempt.objects.create(
                event=event_may,
                nominee=nominee_may,
                amount=Decimal('10.00'),
                currency='GHS',
                platform_commission_percent=event_may.platform_commission_percent,
                vote_quantity=4,
                voter_email='may@example.com',
                gateway_reference='may-ref',
                status=PaymentAttempt.Status.PAID,
            )
            VotePurchase.objects.create(
                event=event_may,
                nominee=nominee_may,
                payment_attempt=attempt_may,
                quantity=4,
                amount_paid=Decimal('10.00'),
                currency='GHS',
                payment_reference='may-ref',
                paid_at=may_time,
            )

        # 2. Current period (June 2026) events and purchase
        june_time = fixed_now - timedelta(days=2) # June 13, 2026
        with patch('django.utils.timezone.now', return_value=june_time):
            event_june1 = self.create_event(status=Event.Status.PUBLISHED, published_at=june_time)
            event_june2 = self.create_event(status=Event.Status.PUBLISHED, published_at=june_time)
            nominee_june = self.create_nominee(event_june1, name='Bob June')
            attempt_june = PaymentAttempt.objects.create(
                event=event_june1,
                nominee=nominee_june,
                amount=Decimal('15.00'),
                currency='GHS',
                platform_commission_percent=event_june1.platform_commission_percent,
                vote_quantity=6,
                voter_email='june@example.com',
                gateway_reference='june-ref',
                status=PaymentAttempt.Status.PAID,
            )
            VotePurchase.objects.create(
                event=event_june1,
                nominee=nominee_june,
                payment_attempt=attempt_june,
                quantity=6,
                amount_paid=Decimal('15.00'),
                currency='GHS',
                payment_reference='june-ref',
                paid_at=june_time,
            )

        # Retrieve context for timeframe='this_month' with mocked now
        with patch('django.utils.timezone.now', return_value=fixed_now):
            context = dashboard_home_context(self.organizer, timeframe='this_month')

        # Assertions for totals
        self.assertEqual(context['summary']['confirmed_votes'], 6)
        self.assertEqual(context['summary']['confirmed_revenue'], Decimal('15.00'))

        # Assertions for comparison trends
        # Events: Current = 2 created (event_june1, event_june2), Previous = 1 created (event_may)
        # Trend calculation: ((2 - 1) / 1) * 100 = +100%
        self.assertTrue(context['comparison']['show_comparison'])
        self.assertEqual(context['comparison']['label'], 'vs last month')

        self.assertTrue(context['comparison']['events']['is_positive'])
        self.assertEqual(context['comparison']['events']['pct'], 100.0)

        # Published: Current = 2 published, Previous = 1 published -> +100%
        self.assertTrue(context['comparison']['published']['is_positive'])
        self.assertEqual(context['comparison']['published']['pct'], 100.0)

        # Votes: Current = 6, Previous = 4
        # Trend: ((6 - 4) / 4) * 100 = 50.0%
        self.assertTrue(context['comparison']['votes']['is_positive'])
        self.assertEqual(context['comparison']['votes']['pct'], 50.0)

        # Revenue: Current = 15.00, Previous = 10.00 -> 50.0%
        self.assertTrue(context['comparison']['revenue']['is_positive'])
        self.assertEqual(context['comparison']['revenue']['pct'], 50.0)
