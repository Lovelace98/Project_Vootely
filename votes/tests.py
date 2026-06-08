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
from ticketing.models import TicketPurchase, TicketType
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

    def post_ussd(self, session_id, user_data, *, new_session=False, msisdn="+233241234567"):
        return self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": session_id,
                "userID": "user-123",
                "msisdn": msisdn,
                "newSession": new_session,
                "userData": user_data,
            }),
            content_type="application/json"
        )

    def create_ticket_event(self, **event_overrides):
        event_data = {
            'owner': self.owner,
            'title': "Ticket Show",
            'kind': Event.Kind.TICKETED_EVENT,
            'status': Event.Status.PUBLISHED,
            'is_public': True,
            'currency': "GHS",
            'venue': "National Theatre",
            'event_date': timezone.now() + timezone.timedelta(days=3),
            'start_at': timezone.now() + timezone.timedelta(days=2),
            'end_at': timezone.now() + timezone.timedelta(days=2, hours=3),
        }
        event_data.update(event_overrides)
        ticket_event = Event.objects.create(**event_data)
        ticket_type = TicketType.objects.create(
            event=ticket_event,
            name="Regular",
            price=Decimal("20.00"),
            quantity_available=50,
            sale_start_at=timezone.now() - timezone.timedelta(hours=1),
            sale_end_at=timezone.now() + timezone.timedelta(days=1),
        )
        return ticket_event, ticket_type

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
        self.assertIn("1. Vote for a nominee", data["message"])
        self.assertIn("2. Buy Tickets", data["message"])
        self.assertIn("3. Cancel", data["message"])

        # Check that session is stored in DB
        session = USSDSession.objects.get(session_id="test-session-001")
        self.assertEqual(session.current_state, "INITIATE")
        self.assertEqual(session.phone_number, "+233241234567")

    def test_select_vote_option_prompts_for_nominee_code(self):
        """Test menu option 1 asks for a nominee code."""
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
            "userData": "1"
        }

        response = self.client.post(
            self.ussd_url,
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Enter Nominee Code", data["message"])

        session.refresh_from_db()
        self.assertEqual(session.current_state, "ENTER_NOMINEE_CODE")

    def test_enter_valid_nominee_code(self):
        """Test entering a valid nominee code transitions state and prompts for votes count."""
        session = USSDSession.objects.create(
            session_id="test-session-002b",
            phone_number="+233241234567",
            current_state="ENTER_NOMINEE_CODE"
        )

        payload = {
            "sessionID": "test-session-002b",
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
            current_state="ENTER_NOMINEE_CODE"
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
        self.assertEqual(session.current_state, "ENTER_NOMINEE_CODE")
        self.assertIsNone(session.nominee_id)

    def test_initial_menu_invalid_option_reprompts(self):
        session = USSDSession.objects.create(
            session_id="test-session-menu-invalid",
            phone_number="+233241234567",
            current_state="INITIATE"
        )

        response = self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": "test-session-menu-invalid",
                "userID": "user-123",
                "msisdn": "+233241234567",
                "newSession": False,
                "userData": "9"
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Invalid option", data["message"])
        self.assertIn("1. Vote for a nominee", data["message"])
        session.refresh_from_db()
        self.assertEqual(session.current_state, "INITIATE")

    def test_initial_menu_cancel_option_ends_session(self):
        USSDSession.objects.create(
            session_id="test-session-menu-cancel",
            phone_number="+233241234567",
            current_state="INITIATE"
        )

        response = self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": "test-session-menu-cancel",
                "userID": "user-123",
                "msisdn": "+233241234567",
                "newSession": False,
                "userData": "3"
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("Thank you for using Vootely", data["message"])
        self.assertFalse(USSDSession.objects.filter(session_id="test-session-menu-cancel").exists())

    def test_buy_ticket_option_accepts_numeric_slug_without_multiple_object_crash(self):
        ticket_event, _ticket_type = self.create_ticket_event(slug=str(self.event.pk))
        session = USSDSession.objects.create(
            session_id="test-session-ticket-numeric",
            phone_number="+233241234567",
            current_state="ENTER_TICKET_EVENT_CODE"
        )

        response = self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": "test-session-ticket-numeric",
                "userID": "user-123",
                "msisdn": "+233241234567",
                "newSession": False,
                "userData": ticket_event.slug,
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Ticket Show", data["message"])
        self.assertIn("1. Buy ticket for self", data["message"])
        session.refresh_from_db()
        self.assertEqual(session.event_id, ticket_event.id)
        self.assertEqual(session.current_state, "SELECT_TICKET_PURCHASE_FOR")

    def test_direct_event_ussd_code_opens_ticket_event_menu(self):
        ticket_event, _ticket_type = self.create_ticket_event()

        response = self.post_ussd(
            "test-session-direct-ticket",
            ticket_event.ussd_dial_code,
            new_session=True,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Ticket Show", data["message"])
        self.assertIn("Venue: National Theatre", data["message"])
        self.assertIn("1. Buy ticket for self", data["message"])
        self.assertIn("2. Buy ticket for someone", data["message"])
        self.assertIn("3. Cancel", data["message"])
        session = USSDSession.objects.get(session_id="test-session-direct-ticket")
        self.assertEqual(session.event_id, ticket_event.id)
        self.assertEqual(session.current_state, "SELECT_TICKET_PURCHASE_FOR")

    def test_direct_event_ussd_code_rejects_invalid_event(self):
        response = self.post_ussd(
            "test-session-direct-invalid",
            "*920*24*9#",
            new_session=True,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("not found or unavailable", data["message"])
        self.assertFalse(USSDSession.objects.filter(session_id="test-session-direct-invalid").exists())

    def test_direct_event_ussd_code_rejects_malformed_suffix(self):
        response = self.post_ussd(
            "test-session-direct-malformed",
            "*920*24*ABC#",
            new_session=True,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("not found or unavailable", data["message"])
        self.assertFalse(USSDSession.objects.filter(session_id="test-session-direct-malformed").exists())

    def test_direct_event_ussd_code_rejects_non_ticket_event(self):
        response = self.post_ussd(
            "test-session-direct-non-ticket",
            self.event.ussd_dial_code,
            new_session=True,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("not found or unavailable", data["message"])

    def test_direct_event_ussd_code_rejects_sold_out_event(self):
        ticket_event, ticket_type = self.create_ticket_event()
        ticket_type.quantity_available = 0
        ticket_type.save(update_fields=['quantity_available'])

        response = self.post_ussd(
            "test-session-direct-sold-out",
            ticket_event.ussd_dial_code,
            new_session=True,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("not found or unavailable", data["message"])

    def test_ticket_event_menu_self_purchase_shows_ticket_types(self):
        ticket_event, _ticket_type = self.create_ticket_event()
        session = USSDSession.objects.create(
            session_id="test-session-ticket-self-menu",
            phone_number="+233241234567",
            current_state="SELECT_TICKET_PURCHASE_FOR",
            event_id=ticket_event.id,
        )

        response = self.post_ussd("test-session-ticket-self-menu", "1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], True)
        self.assertIn("Tickets for Ticket Show", data["message"])
        self.assertIn("1. Regular - GHS 20.00", data["message"])
        session.refresh_from_db()
        self.assertEqual(session.purchase_for, "self")
        self.assertEqual(session.current_state, "SELECT_TICKET_TYPE")

    @patch('votes.views.charge_ticket_momo_stk_push')
    def test_buy_ticket_for_self_charges_dialer(self, mock_charge):
        ticket_event, ticket_type = self.create_ticket_event()
        USSDSession.objects.create(
            session_id="test-session-ticket-self",
            phone_number="+233241234567",
            current_state="SELECT_TICKET_PURCHASE_FOR",
            event_id=ticket_event.id,
        )

        self.post_ussd("test-session-ticket-self", "1")
        self.post_ussd("test-session-ticket-self", "1")
        self.post_ussd("test-session-ticket-self", "2")
        response = self.post_ussd("test-session-ticket-self", "1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("mobile money prompt has been sent", data["message"])
        purchase = TicketPurchase.objects.get(ticket_type=ticket_type)
        self.assertEqual(purchase.buyer_phone, "+233241234567")
        self.assertEqual(purchase.metadata["purchase_for"], "self")
        self.assertNotIn("recipient_phone", purchase.metadata)
        mock_charge.assert_called_once_with(purchase)

    @patch('votes.views.charge_ticket_momo_stk_push')
    def test_buy_ticket_for_someone_charges_dialer_and_stores_recipient(self, mock_charge):
        ticket_event, ticket_type = self.create_ticket_event()
        USSDSession.objects.create(
            session_id="test-session-ticket-someone",
            phone_number="+233241234567",
            current_state="SELECT_TICKET_PURCHASE_FOR",
            event_id=ticket_event.id,
        )

        response = self.post_ussd("test-session-ticket-someone", "2")
        self.assertIn("Enter recipient phone", response.json()["message"])

        response = self.post_ussd("test-session-ticket-someone", "0249998888")
        self.assertIn("+233249998888", response.json()["message"])

        response = self.post_ussd("test-session-ticket-someone", "1")
        self.assertIn("Tickets for Ticket Show", response.json()["message"])

        response = self.post_ussd("test-session-ticket-someone", "1")
        self.assertIn("Enter quantity", response.json()["message"])

        response = self.post_ussd("test-session-ticket-someone", "2")
        self.assertIn("Confirm GHS 41.00", response.json()["message"])

        response = self.post_ussd("test-session-ticket-someone", "1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["continueSession"], False)
        self.assertIn("mobile money prompt has been sent", data["message"])
        purchase = TicketPurchase.objects.get(ticket_type=ticket_type)
        self.assertEqual(purchase.buyer_phone, "+233241234567")
        self.assertEqual(purchase.metadata["purchase_for"], "someone")
        self.assertEqual(purchase.metadata["recipient_phone"], "+233249998888")
        self.assertEqual(purchase.metadata["payer_phone"], "+233241234567")
        mock_charge.assert_called_once_with(purchase)

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

    @patch('votes.views.charge_momo_stk_push')
    def test_failed_vote_stk_marks_payment_attempt_failed(self, mock_charge):
        mock_charge.side_effect = RuntimeError("gateway unavailable")
        USSDSession.objects.create(
            session_id="test-session-vote-charge-fails",
            phone_number="+233241234567",
            current_state="CONFIRM_PAYMENT",
            nominee_id=self.nominee.id,
            votes_count=10,
            amount_due=Decimal("15.00")
        )

        response = self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": "test-session-vote-charge-fails",
                "userID": "user-123",
                "msisdn": "+233241234567",
                "newSession": False,
                "userData": "1"
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        payment = PaymentAttempt.objects.get(voter_phone="+233241234567")
        self.assertEqual(payment.status, PaymentAttempt.Status.FAILED)
        self.assertEqual(payment.gateway_status, "ussd_charge_failed")

    @patch('votes.views.charge_ticket_momo_stk_push')
    def test_failed_ticket_stk_marks_ticket_purchase_failed(self, mock_charge):
        mock_charge.side_effect = RuntimeError("gateway unavailable")
        ticket_event = Event.objects.create(
            owner=self.owner,
            title="Ticket Show",
            kind=Event.Kind.TICKETED_EVENT,
            status=Event.Status.PUBLISHED,
            is_public=True,
            currency="GHS",
            start_at=timezone.now() + timezone.timedelta(days=2),
            end_at=timezone.now() + timezone.timedelta(days=2, hours=3),
        )
        ticket_type = TicketType.objects.create(
            event=ticket_event,
            name="Regular",
            price=Decimal("20.00"),
            quantity_available=50,
            sale_start_at=timezone.now() - timezone.timedelta(hours=1),
            sale_end_at=timezone.now() + timezone.timedelta(days=1),
        )
        USSDSession.objects.create(
            session_id="test-session-ticket-charge-fails",
            phone_number="+233241234567",
            current_state="CONFIRM_TICKET_PAYMENT",
            ticket_type_id=ticket_type.id,
            ticket_quantity=2,
            amount_due=Decimal("40.00"),
        )

        response = self.client.post(
            self.ussd_url,
            data=json.dumps({
                "sessionID": "test-session-ticket-charge-fails",
                "userID": "user-123",
                "msisdn": "+233241234567",
                "newSession": False,
                "userData": "1"
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        purchase = TicketPurchase.objects.get(buyer_phone="+233241234567")
        self.assertEqual(purchase.status, TicketPurchase.Status.FAILED)
        self.assertEqual(purchase.gateway_status, "ussd_charge_failed")

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
