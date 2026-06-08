from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from nominees.models import CompetitionCategory, NominationSubmission, Nominee


class NominationWorkflowTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.organizer = self.user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )
        now = timezone.now()
        self.event = Event.objects.create(
            owner=self.organizer,
            title='Awards Night',
            description='Nomination workflow test event',
            currency='GHS',
            platform_commission_percent=Decimal('10.00'),
            vote_price=Decimal('2.50'),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=2),
            status=Event.Status.PUBLISHED,
            is_public=True,
            published_at=now - timedelta(hours=1),
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        self.category = CompetitionCategory.objects.create(event=self.event, name='Best Student')

    def test_duplicate_pending_submission_is_blocked_for_same_event_category(self):
        NominationSubmission.objects.create(
            event=self.event,
            category=self.category,
            name='Ama',
            email='ama@example.com',
            phone_number='0240000000',
        )

        response = self.client.post(
            reverse('events:nominate', args=[self.event.slug]),
            data={
                'category': self.category.pk,
                'name': 'Ama Again',
                'bio': 'Another submission',
                'email': 'ama@example.com',
                'phone_number': '0240000000',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(NominationSubmission.objects.count(), 1)
        self.assertContains(response, 'A pending or approved submission already exists')

    def test_dashboard_approval_creates_nominee(self):
        submission = NominationSubmission.objects.create(
            event=self.event,
            category=self.category,
            name='Ama',
            email='ama@example.com',
            phone_number='0240000000',
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:nomination_review', args=[self.event.slug, submission.pk]),
            data={
                'category': self.category.pk,
                'name': 'Ama',
                'bio': 'Student leader',
                'email': 'ama@example.com',
                'phone_number': '0240000000',
                'review_notes': 'Approved for launch.',
                'display_order': 1,
                'is_active': 'on',
                'action': 'approve',
            },
            follow=True,
        )

        submission.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submission.status, NominationSubmission.Status.APPROVED)
        self.assertTrue(Nominee.objects.filter(event=self.event, category=self.category, name='Ama').exists())

    def test_dashboard_rejection_keeps_submission_without_nominee(self):
        submission = NominationSubmission.objects.create(
            event=self.event,
            category=self.category,
            name='Kojo',
            email='kojo@example.com',
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:nomination_review', args=[self.event.slug, submission.pk]),
            data={
                'category': self.category.pk,
                'name': 'Kojo',
                'bio': '',
                'email': 'kojo@example.com',
                'phone_number': '',
                'review_notes': 'Not enough details.',
                'display_order': 0,
                'action': 'reject',
            },
            follow=True,
        )

        submission.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submission.status, NominationSubmission.Status.REJECTED)
        self.assertIsNone(submission.approved_nominee)

    def test_dashboard_category_create_shows_duplicate_name_error(self):
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:category_create', args=[self.event.slug]),
            data={
                'name': self.category.name,
                'description': 'Repeat',
                'display_order': 0,
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A category with this name already exists for this event.')

    def test_dashboard_nominee_create_shows_duplicate_name_error_within_category(self):
        Nominee.objects.create(event=self.event, category=self.category, name='Ama', email='ama2@example.com', phone_number='0241111111')
        self.client.login(email=self.organizer.email, password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:nominee_create', args=[self.event.slug]),
            data={
                'category': self.category.pk,
                'name': 'Ama',
                'bio': 'Duplicate nominee',
                'email': 'ama2@example.com',
                'phone_number': '0241111111',
                'display_order': 0,
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A nominee with this name and contact details already exists')

    def test_public_nomination_page_opens_for_draft_event_with_active_window(self):
        now = timezone.now()
        draft_event = Event.objects.create(
            owner=self.organizer,
            title='Draft Awards',
            description='Draft nomination test',
            currency='GHS',
            platform_commission_percent=Decimal('10.00'),
            vote_price=Decimal('2.50'),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=2),
            status=Event.Status.DRAFT,
            is_public=True,
            allow_public_nominations=True,
            nomination_start_at=now - timedelta(hours=1),
            nomination_end_at=now + timedelta(days=1),
        )
        category = CompetitionCategory.objects.create(event=draft_event, name='Best Student')

        response = self.client.get(reverse('events:nominate', args=[draft_event.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Submit nomination')
        self.assertContains(response, category.name)

    def test_public_nomination_page_shows_closed_state_for_closed_event(self):
        self.event.status = Event.Status.CLOSED
        self.event.save(update_fields=['status'])

        response = self.client.get(reverse('events:nominate', args=[self.event.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The event you are submitting a nomination for is closed')

    def test_public_nomination_page_is_unavailable_for_archived_event(self):
        self.event.status = Event.Status.ARCHIVED
        self.event.save(update_fields=['status'])

        response = self.client.get(reverse('events:nominate', args=[self.event.slug]))

        self.assertEqual(response.status_code, 404)

    def test_public_nomination_submission_is_rate_limited(self):
        cache.clear()
        url = reverse('events:nominate', args=[self.event.slug])
        for index in range(10):
            response = self.client.post(
                url,
                data={
                    'category': self.category.pk,
                    'name': f'Nominee {index}',
                    'bio': 'Self nomination',
                    'email': f'nominee{index}@example.com',
                    'phone_number': f'02400000{index:02d}',
                },
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            url,
            data={
                'category': self.category.pk,
                'name': 'Blocked nominee',
                'bio': 'Self nomination',
                'email': 'blocked@example.com',
                'phone_number': '0249999999',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Too many nomination attempts from this network')

    def test_nominee_delete_with_votes_protected(self):
        from payments.models import PaymentAttempt
        nominee = Nominee.objects.create(event=self.event, category=self.category, name='ToDelete')
        PaymentAttempt.objects.create(
            event=self.event,
            nominee=nominee,
            amount=Decimal('5.00'),
            currency='GHS',
            platform_commission_percent=Decimal('10.00'),
            vote_quantity=2,
            voter_email='voter@example.com',
            gateway_reference='ref-nom-del',
            status=PaymentAttempt.Status.PAID,
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(
            reverse('dashboard:nominee_delete', args=[self.event.slug, nominee.slug]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Nominee.objects.filter(pk=nominee.pk).exists())
        self.assertContains(response, 'This nominee cannot be deleted because they have associated payments or votes.')

    def test_nomination_approval_copies_photo_to_nominees_directory(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9'
            b'\x04\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00'
            b'\x00\x02\x02\x4c\x01\x00\x3b'
        )
        photo = SimpleUploadedFile('test_photo.gif', small_gif, content_type='image/gif')
        submission = NominationSubmission.objects.create(
            event=self.event,
            category=self.category,
            name='Self Nominated Photo',
            email='photo@example.com',
            phone_number='0249991111',
            photo=photo,
        )
        self.assertTrue(submission.photo.name.startswith('nomination-submissions/'))
        
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(
            reverse('dashboard:nomination_review', args=[self.event.slug, submission.pk]),
            data={
                'category': self.category.pk,
                'name': 'Self Nominated Photo',
                'bio': 'Test bio',
                'email': 'photo@example.com',
                'phone_number': '0249991111',
                'review_notes': 'Approve and test photo migration',
                'display_order': 1,
                'is_active': 'on',
                'photo': submission.photo.name,
                'action': 'approve',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        submission.refresh_from_db()
        self.assertEqual(submission.status, NominationSubmission.Status.APPROVED)
        nominee = submission.approved_nominee
        self.assertIsNotNone(nominee)
        self.assertTrue(nominee.photo.name.startswith('nominees/'))
        self.assertEqual(nominee.photo.read(), small_gif)
        
        # Clean up files from disk
        if submission.photo:
            submission.photo.storage.delete(submission.photo.name)
        if nominee.photo:
            nominee.photo.storage.delete(nominee.photo.name)

    def test_nominee_creation_with_same_name_different_contacts_succeeds(self):
        Nominee.objects.create(
            event=self.event,
            category=self.category,
            name='Kofi Mensah',
            email='kofi1@example.com',
            phone_number='0241112222',
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(
            reverse('dashboard:nominee_create', args=[self.event.slug]),
            data={
                'category': self.category.pk,
                'name': 'Kofi Mensah',
                'bio': 'Second nominee with same name',
                'email': 'kofi2@example.com',
                'phone_number': '0243334444',
                'display_order': 0,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Nominee.objects.filter(event=self.event, category=self.category, name='Kofi Mensah').count(), 2)

    def test_nominee_creation_with_same_name_matching_email_fails(self):
        Nominee.objects.create(
            event=self.event,
            category=self.category,
            name='Kofi Mensah',
            email='kofi@example.com',
            phone_number='0241112222',
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(
            reverse('dashboard:nominee_create', args=[self.event.slug]),
            data={
                'category': self.category.pk,
                'name': 'Kofi Mensah',
                'bio': 'Second nominee',
                'email': 'kofi@example.com',
                'phone_number': '0243334444',
                'display_order': 0,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A nominee with this name and contact details already exists')

    def test_nominee_creation_with_same_name_both_empty_contacts_fails(self):
        Nominee.objects.create(
            event=self.event,
            category=self.category,
            name='Kofi Mensah',
            email='',
            phone_number='',
        )
        self.client.login(email=self.organizer.email, password='strong-pass-123')
        response = self.client.post(
            reverse('dashboard:nominee_create', args=[self.event.slug]),
            data={
                'category': self.category.pk,
                'name': 'Kofi Mensah',
                'bio': 'Second nominee',
                'email': '',
                'phone_number': '',
                'display_order': 0,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A nominee with this name and contact details already exists')
