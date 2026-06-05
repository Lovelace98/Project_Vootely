import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import Event
from nominees.models import CompetitionCategory, Nominee
from payments.models import PaymentAttempt
from votes.models import USSDSession, VotePurchase


class ArkeselUSSDTestCase(TestCase):
    def setUp(self):
        # 1. Create a user/owner
        self.owner = User.objects.create_user(
            email='organizer@example.com',
            password='password123',
            is_active=True
        )

        # 2. Create a published paid competition event
        self.event = Event.objects.create(
            owner=self.owner,
            title="Annual Music Awards",
            kind=Event.Kind.PAID_COMPETITION,
            status=Event.Status.PUBLISHED,
            is_public=True,
            currency="GHS",
            vote_price=Decimal("1.50"),
            platform_commission_percent=Decimal("10.00"),
            start_at=timezone.now() - timezone.timedelta(days=1),
            end_at=timezone.now() + timezone.timedelta(days=1),
        )

        # 3. Create a competition category
        self.category = CompetitionCategory.objects.create(
            event=self.event,
            name="Best Artist",
            slug="best-artist"
        )

        # 4. Create a nominee
        # By default, nominee.code will be generated as a 5-char code on save
        self.nominee = Nominee.objects.create(
            event=self.event,
            category=self.category,
            name="Sarkodie",
            slug="sarkodie"
        )
        # Ensure code is upper
        self.nominee_code = self.nominee.code.upper()

        self.ussd_url = reverse('votes:ussd_callback')

    def test_initiate_ussd_session(self):
        """Test dialing the USSD shortcode for the first time."""
        payload = {
            "sessionID": "test-session-001",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": True,
            "userData": "*920*24#"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["sessionID"], "test-session-001")
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Welcome to Vootely!", data["message"])
        self.assertIn("Enter Nominee Code", data["message"])

        # Check that session is stored in DB
        session = USSDSession.objects.get(session_id="test-session-001")
        self.assertEqual(session.current_state, "INITIATE")
        self.assertEqual(session.phone_number, "+233241234567")

    def test_enter_valid_nominee_code(self):
        """Test entering a valid nominee code transitions state and prompts for votes count."""
        # Pre-create the session
        session = USSDSession.objects.create(
            session_id="test-session-002",
            phone_number="+233241234567",
            current_state="INITIATE"
        )

        payload = {
            "sessionID": "test-session-002",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": self.nominee_code
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn(f"Vote for {self.nominee.name}", data["message"])
        self.assertIn("Enter number of votes", data["message"])

        # Check DB updates
        session.refresh_from_db()
        self.assertEqual(session.current_state, "ENTER_VOTES")
        self.assertEqual(session.nominee_id, self.nominee.id)

    def test_enter_invalid_nominee_code(self):
        """Test entering an invalid nominee code displays error and keeps user in INITIATE state."""
        session = USSDSession.objects.create(
            session_id="test-session-003",
            phone_number="+233241234567",
            current_state="INITIATE"
        )

        payload = {
            "sessionID": "test-session-003",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "WRONG"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Nominee not found", data["message"])

        session.refresh_from_db()
        self.assertEqual(session.current_state, "INITIATE")
        self.assertIsNone(session.nominee_id)

    def test_enter_valid_vote_count(self):
        """Test entering a valid vote count transitions to confirmation screen."""
        session = USSDSession.objects.create(
            session_id="test-session-004",
            phone_number="+233241234567",
            current_state="ENTER_VOTES",
            nominee_id=self.nominee.id
        )

        payload = {
            "sessionID": "test-session-004",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "10"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        # Cost is 10 votes * 1.50 = GHS 15.00
        self.assertIn("Confirm GHS 15.00 for 10 votes", data["message"])
        self.assertIn("1. Confirm & Pay", data["message"])

        session.refresh_from_db()
        self.assertEqual(session.current_state, "CONFIRM_PAYMENT")
        self.assertEqual(session.votes_count, 10)
        self.assertEqual(session.amount_due, Decimal("15.00"))

    def test_enter_invalid_vote_count(self):
        """Test entering an invalid votes count asks user to retry and keeps state."""
        session = USSDSession.objects.create(
            session_id="test-session-005",
            phone_number="+233241234567",
            current_state="ENTER_VOTES",
            nominee_id=self.nominee.id
        )

        payload = {
            "sessionID": "test-session-005",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "abc"  # Invalid integer
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Invalid number", data["message"])

        session.refresh_from_db()
        self.assertEqual(session.current_state, "ENTER_VOTES")

    def test_enter_vote_count_exceeding_limit(self):
        """Test entering a vote count that makes the amount due exceed GHS 10,000 asks user to retry and keeps state."""
        session = USSDSession.objects.create(
            session_id="test-session-009",
            phone_number="+233241234567",
            current_state="ENTER_VOTES",
            nominee_id=self.nominee.id
        )

        payload = {
            "sessionID": "test-session-009",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "10000"  # 10,000 votes * 1.50 = GHS 15,000 (exceeds 10,000 limit)
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Amount exceeds MoMo limits", data["message"])

        session.refresh_from_db()
        self.assertEqual(session.current_state, "ENTER_VOTES")

    @patch('votes.views.charge_momo_stk_push')
    def test_confirm_payment_option_1(self, mock_charge):
        """Test option '1' (Confirm & Pay) triggers Paystack STK push, creates PaymentAttempt, and ends session."""
        session = USSDSession.objects.create(
            session_id="test-session-006",
            phone_number="+233241234567",
            current_state="CONFIRM_PAYMENT",
            nominee_id=self.nominee.id,
            votes_count=10,
            amount_due=Decimal("15.00")
        )

        payload = {
            "sessionID": "test-session-006",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "1"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("mobile money prompt has been sent", data["message"])

        # Check DB states
        # USSD Session should be deleted
        self.assertFalse(USSDSession.objects.filter(session_id="test-session-006").exists())

        # PaymentAttempt should be created
        payment = PaymentAttempt.objects.get(
            event=self.event,
            nominee=self.nominee,
            voter_phone="+233241234567",
            amount=Decimal("15.00"),
            vote_quantity=10
        )
        self.assertEqual(payment.status, PaymentAttempt.Status.INITIALIZED)

        # STK Push service should have been called
        mock_charge.assert_called_once_with(payment)

    def test_cancel_payment_option_2(self):
        """Test option '2' (Cancel) deletes the session and terminates without charging."""
        session = USSDSession.objects.create(
            session_id="test-session-007",
            phone_number="+233241234567",
            current_state="CONFIRM_PAYMENT",
            nominee_id=self.nominee.id,
            votes_count=10,
            amount_due=Decimal("15.00")
        )

        payload = {
            "sessionID": "test-session-007",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "2"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("Voting cancelled", data["message"])

        # Session should be deleted
        self.assertFalse(USSDSession.objects.filter(session_id="test-session-007").exists())
        # No PaymentAttempt should be created
        self.assertFalse(PaymentAttempt.objects.filter(voter_phone="+233241234567").exists())

    def test_confirm_payment_invalid_option(self):
        """Test entering an invalid option on confirmation screen prompts the user again and keeps state."""
        session = USSDSession.objects.create(
            session_id="test-session-008",
            phone_number="+233241234567",
            current_state="CONFIRM_PAYMENT",
            nominee_id=self.nominee.id,
            votes_count=10,
            amount_due=Decimal("15.00")
        )

        payload = {
            "sessionID": "test-session-008",
            "userID": "user-123",
            "msisdn": "+233241234567",
            "newSession": False,
            "userData": "3"  # Invalid option
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Invalid option", data["message"])
        self.assertIn("Confirm GHS 15.00 for 10 votes", data["message"])

        # Session should still exist in DB and remain in CONFIRM_PAYMENT state
        session.refresh_from_db()
        self.assertEqual(session.current_state, "CONFIRM_PAYMENT")

