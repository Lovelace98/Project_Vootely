from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from events.models import Event


class CustomUserAuthTests(TestCase):
    def test_login_with_email_only_credentials(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )

        authenticated = self.client.login(
            email='organizer@example.com',
            password='strong-pass-123',
        )

        self.assertTrue(authenticated)

    def test_seed_phase1_demo_command_creates_demo_data(self):
        call_command('seed_phase1_demo', email='seed@example.com', password='seed-pass-123')

        user_model = get_user_model()
        self.assertTrue(user_model.objects.filter(email='seed@example.com').exists())
        self.assertTrue(Event.objects.filter(slug='demo-campus-star').exists())

    def test_notification_settings_requires_login(self):
        response = self.client.get(reverse('dashboard:notification_settings'))

        self.assertEqual(response.status_code, 302)

    def test_notification_settings_normalizes_phone_number(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            email='notify@example.com',
            password='strong-pass-123',
        )
        self.client.login(email='notify@example.com', password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:notification_settings'),
            data={'phone_number': '0241 234 567', 'sms_opt_in': 'on'},
            follow=True,
        )

        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.phone_number, '+233241234567')
        self.assertTrue(user.sms_opt_in)

    def test_signup_onboarding_saves_profiling_and_triggers_verification(self):
        # Execute signup via the new high-fidelity signup form
        response = self.client.post(
            reverse('account_signup'),
            data={
                'email': 'integration_org@example.com',
                'password1': 'Strong-Pass-123',
                'password2': 'Strong-Pass-123',
                'first_name': 'Ama',
                'last_name': 'Kyeremeh',
                'organizer_type': 'company',
                'referral_source': 'social_media',
                'referral_source_other': '',
                'agree_to_terms': True
            },
            follow=True
        )
        
        # Under ACCOUNT_EMAIL_VERIFICATION = 'mandatory', allauth redirects to the verification sent page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirm your email')
        
        # Verify the database entry has correct custom profiling information
        user_model = get_user_model()
        user = user_model.objects.get(email='integration_org@example.com')
        self.assertEqual(user.first_name, 'Ama')
        self.assertEqual(user.last_name, 'Kyeremeh')
        self.assertEqual(user.organizer_type, 'company')
        self.assertEqual(user.referral_source, 'Social Media (Facebook, Twitter, etc.)')

    def test_signup_fails_without_agree_to_terms(self):
        # Sign up without checking the terms checkbox
        response = self.client.post(
            reverse('account_signup'),
            data={
                'email': 'terms_fail_org@example.com',
                'password1': 'Strong-Pass-123',
                'password2': 'Strong-Pass-123',
                'first_name': 'Ama',
                'last_name': 'Kyeremeh',
                'organizer_type': 'company',
                'referral_source': 'social_media',
                'referral_source_other': '',
                'agree_to_terms': False
            },
            follow=True
        )
        
        # Verify the response is 200 (form re-renders with errors) but contains the validation error message
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You must agree to the Terms of Service and Privacy Policy to register.')
        
        # Verify that no user was created
        user_model = get_user_model()
        self.assertFalse(user_model.objects.filter(email='terms_fail_org@example.com').exists())


