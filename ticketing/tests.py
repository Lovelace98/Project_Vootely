from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from wallets.models import LedgerEntry

from .models import Ticket, TicketCheckIn, TicketProvisionalEntry, TicketPurchase, TicketScannerPass, TicketType, scanner_pass_default_expiry
from .services import check_in_ticket, create_ticket_purchase, handle_ticket_paystack_webhook


class TicketingFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )
        self.staff = get_user_model().objects.create_user(
            email='staff@example.com',
            password='strong-pass-123',
        )
        now = timezone.now()
        self.event = Event.objects.create(
            owner=self.user,
            title='Vootely Live Night',
            kind=Event.Kind.TICKETED_EVENT,
            currency='GHS',
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=4),
            status=Event.Status.DRAFT,
            is_public=True,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event,
            name='Regular',
            price=Decimal('100.00'),
            quantity_available=50,
            sale_start_at=now - timedelta(hours=1),
            sale_end_at=now + timedelta(days=2),
            max_per_order=5,
            is_active=True,
        )

    def test_ticketed_event_publish_requires_ticket_type(self):
        empty_event = Event.objects.create(
            owner=self.user,
            title='Empty Ticket Event',
            kind=Event.Kind.TICKETED_EVENT,
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=3),
        )

        allowed, errors = empty_event.can_publish()

        self.assertFalse(allowed)
        self.assertIn('Add at least one active ticket type.', errors)

    def test_ticketed_event_can_publish_without_vote_setup(self):
        self.event.publish()

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.PUBLISHED)
        self.assertIsNone(self.event.vote_price)
        self.assertIsNone(self.event.platform_commission_percent)

    def test_event_public_code_is_generated_without_revealing_internal_id(self):
        self.event.refresh_from_db()

        self.assertRegex(self.event.public_code, r'^V-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}$')
        self.assertNotEqual(self.event.public_code, f'V{self.event.id:04d}')

    @override_settings(USSD_SHORT_CODE='*920*24#')
    def test_event_ussd_code_is_generated_and_formats_direct_dial_code(self):
        self.event.refresh_from_db()

        self.assertGreaterEqual(self.event.ussd_code, 10)
        self.assertLessEqual(self.event.ussd_code, 999)
        self.assertEqual(self.event.ussd_dial_code, f'*920*24*{self.event.ussd_code}#')

    def test_ticket_purchase_snapshots_default_seven_percent_commission(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=2,
            buyer_email='buyer@example.com',
        )

        self.assertEqual(purchase.amount, Decimal('205.00'))
        self.assertEqual(purchase.buyer_handling_fee, Decimal('5.00'))
        self.assertEqual(purchase.ticket_commission_percent, Decimal('7.00'))

    def test_ticket_type_rejects_zero_price(self):
        self.ticket_type.price = Decimal('0.00')

        with self.assertRaises(ValidationError):
            self.ticket_type.full_clean()

    def test_ticket_type_duplicate_name_is_validation_error(self):
        duplicate = TicketType(
            event=self.event,
            name='regular',
            price=Decimal('50.00'),
            quantity_available=10,
            sale_start_at=timezone.now() - timedelta(hours=1),
            sale_end_at=timezone.now() + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_bad_ticket_quantity_returns_validation_error_not_crash(self):
        allowed, reason = self.ticket_type.can_purchase('abc')

        self.assertFalse(allowed)
        self.assertEqual(reason, 'Choose a valid ticket quantity.')

    def test_successful_webhook_issues_tickets_and_posts_ledger_once(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=2,
            buyer_name='Ama Buyer',
            buyer_email='buyer@example.com',
            buyer_phone='0240000000',
        )
        payload = {
            'event': 'charge.success',
            'data': {
                'reference': purchase.gateway_reference,
                'amount': 20500,
                'currency': 'GHS',
                'status': 'success',
                'customer': {'email': 'buyer@example.com', 'phone': '0240000000'},
                'metadata': {'buyer_name': 'Ama Buyer'},
            },
        }

        handle_ticket_paystack_webhook(payload)
        handle_ticket_paystack_webhook(payload)

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, TicketPurchase.Status.PAID)
        self.assertEqual(purchase.tickets.count(), 2)
        self.assertEqual(LedgerEntry.objects.filter(transaction__ticket_purchase=purchase).count(), 3)
        self.assertEqual(
            LedgerEntry.objects.get(
                transaction__ticket_purchase=purchase,
                account__owner=self.user,
                kind=LedgerEntry.Kind.ORGANIZER_SALE_CREDIT,
            ).amount,
            Decimal('186.00'),
        )

    def test_ticket_commission_locks_after_paid_ticket(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_email='buyer@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.completed_at = timezone.now()
        purchase.save(update_fields=['status', 'completed_at'])

        self.event.ticket_commission_percent = Decimal('8.00')

        with self.assertRaises(ValidationError):
            self.event.full_clean()

    def test_check_in_accepts_valid_ticket_and_rejects_duplicate(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_email='buyer@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        ticket = Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        first = check_in_ticket(event=self.event, code=ticket.code, user=self.staff)
        second = check_in_ticket(event=self.event, code=ticket.code, user=self.staff)

        ticket.refresh_from_db()
        self.assertTrue(first['ok'])
        self.assertFalse(second['ok'])
        self.assertEqual(ticket.status, Ticket.Status.USED)
        self.assertEqual(ticket.checked_in_by, self.staff)

    def test_wrong_event_check_in_does_not_disclose_buyer_details(self):
        other_event = Event.objects.create(
            owner=self.user,
            title='Other Event',
            kind=Event.Kind.TICKETED_EVENT,
            start_at=timezone.now() + timedelta(days=4),
            end_at=timezone.now() + timedelta(days=4, hours=3),
        )
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name='Private Buyer',
            buyer_email='private@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        ticket = Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        result = check_in_ticket(event=other_event, code=ticket.code, user=self.staff)

        self.assertFalse(result['ok'])
        self.assertNotIn('buyer_name', result)
        self.assertNotIn('ticket_type', result)
        self.assertFalse(ticket.checkins.filter(event=other_event).exists())

    def test_duplicate_check_in_response_includes_same_event_ticket_details(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name='Ama Buyer',
            buyer_email='ama@example.com',
            buyer_phone='0240000000',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        ticket = Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        check_in_ticket(event=self.event, code=ticket.code, user=self.staff)
        duplicate = check_in_ticket(event=self.event, code=ticket.code, user=self.staff)

        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['buyer_name'], 'Ama Buyer')
        self.assertEqual(duplicate['buyer_phone'], '0240000000')
        self.assertEqual(duplicate['ticket_type'], self.ticket_type.name)
        self.assertTrue(duplicate['used_at'])

    def test_paid_attendees_page_lists_ticket_ids_and_check_in_status(self):
        self.client.force_login(self.user)
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=2,
            buyer_name='Ama Buyer',
            buyer_email='ama@example.com',
            buyer_phone='0240000000',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.completed_at = timezone.now()
        purchase.save(update_fields=['status', 'completed_at'])
        first_ticket = Ticket.objects.create(purchase=purchase, ticket_type=self.ticket_type, event=self.event)
        second_ticket = Ticket.objects.create(purchase=purchase, ticket_type=self.ticket_type, event=self.event)
        check_in_ticket(event=self.event, code=first_ticket.code, user=self.staff)

        response = self.client.get(reverse('dashboard:ticket_attendees', args=[self.event.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ama Buyer')
        self.assertContains(response, first_ticket.code)
        self.assertContains(response, second_ticket.code)
        self.assertContains(response, 'Checked in')

    def test_check_in_launcher_redirects_by_event_code_for_owner(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('dashboard:ticket_check_in_launch'), {'event_id': self.event.public_code.lower()})

        self.assertRedirects(response, reverse('dashboard:ticket_check_in', args=[self.event.slug]))

    def test_check_in_launcher_accepts_event_code_without_separator(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('dashboard:ticket_check_in_launch'),
            {'event_id': self.event.public_code.replace('-', '')},
        )

        self.assertRedirects(response, reverse('dashboard:ticket_check_in', args=[self.event.slug]))

    def create_paid_ticket(self, buyer_name='Ama Buyer'):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name=buyer_name,
            buyer_email='buyer@example.com',
            buyer_phone='0240000000',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.completed_at = timezone.now()
        purchase.save(update_fields=['status', 'completed_at'])
        return Ticket.objects.create(purchase=purchase, ticket_type=self.ticket_type, event=self.event)

    def create_scanner_pass(self, **overrides):
        defaults = {
            'event': self.event,
            'gate_name': 'Gate A',
            'staff_label': 'Ama Gate',
            'pin_hash': make_password('1234'),
            'expires_at': self.event.end_at + timedelta(hours=12),
            'created_by': self.user,
        }
        defaults.update(overrides)
        return TicketScannerPass.objects.create(**defaults)

    def test_organizer_can_create_view_reset_and_revoke_scanner_pass(self):
        self.client.force_login(self.user)

        create_response = self.client.post(
            reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]),
            {'gate_name': 'Gate B', 'staff_label': 'Kojo', 'pin': '2468'},
        )

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(create_response['Location'], reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        scanner_pass = TicketScannerPass.objects.get(gate_name='Gate B')
        list_response = self.client.get(reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        self.assertContains(list_response, 'Gate B')
        self.assertContains(list_response, '2468')
        self.assertContains(list_response, 'Copy full message')
        self.assertContains(list_response, scanner_pass.get_absolute_url())
        second_list_response = self.client.get(reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        self.assertNotContains(second_list_response, '2468')

        scanner_pass.device_session_key = 'old-device'
        scanner_pass.activated_at = timezone.now()
        scanner_pass.save(update_fields=['device_session_key', 'activated_at'])
        token_before_device_reset = scanner_pass.token
        reset_response = self.client.post(
            reverse('dashboard:ticket_scanner_pass_action', args=[self.event.slug, scanner_pass.pk, 'reset'])
        )
        self.assertRedirects(reset_response, reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        scanner_pass.refresh_from_db()
        self.assertEqual(scanner_pass.device_session_key, '')
        self.assertIsNone(scanner_pass.activated_at)
        self.assertEqual(scanner_pass.token, token_before_device_reset)

        toggle_response = self.client.post(
            reverse('dashboard:ticket_scanner_pass_action', args=[self.event.slug, scanner_pass.pk, 'toggle_provisional'])
        )
        self.assertRedirects(toggle_response, reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        scanner_pass.refresh_from_db()
        self.assertTrue(scanner_pass.allow_provisional_entry)

        revoke_response = self.client.post(
            reverse('dashboard:ticket_scanner_pass_action', args=[self.event.slug, scanner_pass.pk, 'revoke'])
        )
        self.assertRedirects(revoke_response, reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        scanner_pass.refresh_from_db()
        self.assertEqual(scanner_pass.status, TicketScannerPass.Status.REVOKED)
        self.assertIsNotNone(scanner_pass.revoked_at)

    def test_scanner_pass_form_does_not_echo_pin_after_validation_error(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]),
            {'gate_name': '', 'staff_label': 'Kojo', 'pin': '9876'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '9876')

    def test_scanner_pass_credential_reset_rotates_token_and_shows_one_time_share(self):
        self.client.force_login(self.user)
        scanner_pass = self.create_scanner_pass()
        old_token = scanner_pass.token
        old_url = reverse('ticketing:scanner_pass', args=[old_token])
        scanner_pass.device_session_key = 'old-device'
        scanner_pass.activated_at = timezone.now()
        scanner_pass.save(update_fields=['device_session_key', 'activated_at'])

        response = self.client.post(
            reverse('dashboard:ticket_scanner_pass_action', args=[self.event.slug, scanner_pass.pk, 'reset_credentials']),
            {'pin': '7777'},
        )

        self.assertEqual(response.status_code, 302)
        scanner_pass.refresh_from_db()
        self.assertNotEqual(scanner_pass.token, old_token)
        self.assertEqual(scanner_pass.device_session_key, '')
        self.assertIsNone(scanner_pass.activated_at)
        self.assertEqual(scanner_pass.expires_at, scanner_pass_default_expiry(self.event))
        share_response = self.client.get(reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        self.assertContains(share_response, '7777')
        self.assertContains(share_response, scanner_pass.get_absolute_url())
        self.assertNotContains(share_response, old_url)
        second_share_response = self.client.get(reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        self.assertNotContains(second_share_response, '7777')

        staff_client = self.client_class()
        self.assertEqual(staff_client.post(old_url, {'pin': '1234'}).status_code, 404)
        new_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        self.assertEqual(staff_client.post(new_url, {'pin': '1234'}).status_code, 403)
        self.assertEqual(staff_client.post(new_url, {'pin': '7777'}).status_code, 302)

    def test_staff_scanner_requires_correct_pin_and_binds_one_device(self):
        scanner_pass = self.create_scanner_pass()
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])

        wrong_response = self.client.post(scanner_url, {'pin': '9999'})
        self.assertEqual(wrong_response.status_code, 403)

        valid_response = self.client.post(scanner_url, {'pin': '1234'})
        self.assertRedirects(valid_response, scanner_url)
        scanner_pass.refresh_from_db()
        self.assertTrue(scanner_pass.device_session_key)
        self.assertIsNotNone(scanner_pass.activated_at)

        second_client = self.client_class()
        blocked_response = second_client.post(scanner_url, {'pin': '1234'})
        self.assertEqual(blocked_response.status_code, 403)

    def test_staff_scanner_pin_activation_is_rate_limited(self):
        cache.clear()
        scanner_pass = self.create_scanner_pass()
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])

        for _idx in range(5):
            response = self.client.post(scanner_url, {'pin': '9999'})
            self.assertEqual(response.status_code, 403)
        limited_response = self.client.post(scanner_url, {'pin': '9999'})

        self.assertEqual(limited_response.status_code, 429)
        self.assertContains(limited_response, 'Too many PIN attempts', status_code=429)

    def test_staff_scanner_checks_in_ticket_and_logs_gate_metadata(self):
        ticket = self.create_paid_ticket()
        scanner_pass = self.create_scanner_pass(gate_name='Main Gate', staff_label='Akua')
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        scan_url = reverse('ticketing:scanner_pass_scan', args=[scanner_pass.token])

        self.client.post(scanner_url, {'pin': '1234'})
        response = self.client.post(scan_url, {'code': ticket.code})

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.USED)
        self.assertIsNone(ticket.checked_in_by)
        checkin = ticket.checkins.latest('scanned_at')
        self.assertEqual(checkin.scanner_pass, scanner_pass)
        self.assertEqual(checkin.scanner_gate_name, 'Main Gate')
        self.assertEqual(checkin.scanner_staff_label, 'Akua')

    def test_scanner_pass_provisional_sync_requires_enabled_pass(self):
        ticket = self.create_paid_ticket()
        scanner_pass = self.create_scanner_pass()
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[scanner_pass.token])
        self.client.post(scanner_url, {'pin': '1234'})

        response = self.client.post(
            sync_url,
            data={
                'attempts': [
                    {
                        'client_attempt_id': 'attempt-disabled',
                        'ticket_code': ticket.code,
                        'offline_at': timezone.now().isoformat(),
                        'device_id': 'device-1',
                    }
                ]
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.ACTIVE)
        self.assertFalse(TicketProvisionalEntry.objects.exists())

    def test_scanner_pass_provisional_sync_rejects_empty_payload(self):
        scanner_pass = self.create_scanner_pass(allow_provisional_entry=True)
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[scanner_pass.token])
        self.client.post(scanner_url, {'pin': '1234'})

        response = self.client.post(sync_url, data={}, content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'No provisional attempts were provided.')
        self.assertFalse(TicketProvisionalEntry.objects.exists())

    def test_scanner_pass_provisional_sync_confirms_and_is_idempotent(self):
        ticket = self.create_paid_ticket()
        scanner_pass = self.create_scanner_pass(gate_name='Main Gate', staff_label='Akua', allow_provisional_entry=True)
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[scanner_pass.token])
        self.client.post(scanner_url, {'pin': '1234'})
        payload = {
            'attempts': [
                {
                    'client_attempt_id': 'attempt-confirm',
                    'ticket_code': ticket.code,
                    'offline_at': timezone.now().isoformat(),
                    'device_id': 'device-1',
                    'ticket_snapshot': {'buyer_name': 'Ama Buyer'},
                }
            ]
        }

        response = self.client.post(sync_url, data=payload, content_type='application/json')
        retry_response = self.client.post(sync_url, data=payload, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['status'], TicketProvisionalEntry.Status.CONFIRMED)
        self.assertEqual(response.json()['results'][0]['result'], TicketProvisionalEntry.Result.CONFIRMED)
        self.assertEqual(retry_response.json()['results'][0]['status'], TicketProvisionalEntry.Status.CONFIRMED)
        self.assertEqual(TicketProvisionalEntry.objects.count(), 1)
        self.assertEqual(ticket.checkins.count(), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.USED)
        entry = TicketProvisionalEntry.objects.get()
        self.assertEqual(entry.scanner_pass, scanner_pass)
        self.assertEqual(entry.gate_name, 'Main Gate')
        self.assertEqual(entry.staff_label, 'Akua')
        self.assertIsNotNone(entry.final_checkin)

    def test_scanner_pass_provisional_sync_does_not_replay_another_pass_attempt(self):
        ticket = self.create_paid_ticket()
        first_pass = self.create_scanner_pass(gate_name='Main Gate', allow_provisional_entry=True)
        second_pass = self.create_scanner_pass(gate_name='Side Gate', allow_provisional_entry=True)
        first_url = reverse('ticketing:scanner_pass', args=[first_pass.token])
        first_sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[first_pass.token])
        second_url = reverse('ticketing:scanner_pass', args=[second_pass.token])
        second_sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[second_pass.token])
        payload = {
            'attempts': [
                {
                    'client_attempt_id': 'attempt-shared',
                    'ticket_code': ticket.code,
                    'offline_at': timezone.now().isoformat(),
                    'device_id': 'device-1',
                }
            ]
        }
        self.client.post(first_url, {'pin': '1234'})
        self.client.post(first_sync_url, data=payload, content_type='application/json')
        self.client.post(second_url, {'pin': '1234'})

        response = self.client.post(second_sync_url, data=payload, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        result = response.json()['results'][0]
        self.assertEqual(result['status'], TicketProvisionalEntry.Status.REJECTED)
        self.assertEqual(result['result'], TicketProvisionalEntry.Result.UNAUTHORIZED_REJECTED)
        self.assertEqual(TicketProvisionalEntry.objects.count(), 1)
        self.assertEqual(TicketProvisionalEntry.objects.get().scanner_pass, first_pass)

    def test_scanner_pass_provisional_sync_rejects_duplicate_ticket(self):
        ticket = self.create_paid_ticket()
        ticket.status = Ticket.Status.USED
        ticket.used_at = timezone.now()
        ticket.save(update_fields=['status', 'used_at'])
        scanner_pass = self.create_scanner_pass(allow_provisional_entry=True)
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        sync_url = reverse('ticketing:scanner_pass_provisional_sync', args=[scanner_pass.token])
        self.client.post(scanner_url, {'pin': '1234'})

        response = self.client.post(
            sync_url,
            data={
                'attempts': [
                    {
                        'client_attempt_id': 'attempt-duplicate',
                        'ticket_code': ticket.code,
                        'offline_at': timezone.now().isoformat(),
                        'device_id': 'device-1',
                    }
                ]
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()['results'][0]
        self.assertEqual(result['status'], TicketProvisionalEntry.Status.REJECTED)
        self.assertEqual(result['result'], TicketProvisionalEntry.Result.DUPLICATE_REJECTED)
        self.assertEqual(TicketProvisionalEntry.objects.get().status, TicketProvisionalEntry.Status.REJECTED)

    def test_organizer_provisional_sync_confirms_ticket_without_scanner_pass(self):
        ticket = self.create_paid_ticket()
        self.client.force_login(self.user)
        sync_url = reverse('dashboard:ticket_check_in_provisional_sync', args=[self.event.slug])

        response = self.client.post(
            sync_url,
            data={
                'attempts': [
                    {
                        'client_attempt_id': 'organizer-attempt',
                        'ticket_code': ticket.code,
                        'offline_at': timezone.now().isoformat(),
                        'device_id': 'organizer-device',
                    }
                ]
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['status'], TicketProvisionalEntry.Status.CONFIRMED)
        entry = TicketProvisionalEntry.objects.get(client_attempt_id='organizer-attempt')
        self.assertEqual(entry.checked_in_by, self.user)
        self.assertIsNone(entry.scanner_pass)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.USED)

    def test_organizer_provisional_sync_rejects_empty_payload(self):
        self.client.force_login(self.user)
        sync_url = reverse('dashboard:ticket_check_in_provisional_sync', args=[self.event.slug])

        response = self.client.post(sync_url, data={}, content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'No provisional attempts were provided.')
        self.assertFalse(TicketProvisionalEntry.objects.exists())

    def test_staff_scanner_rejects_duplicate_wrong_event_and_inactive_passes(self):
        ticket = self.create_paid_ticket()
        scanner_pass = self.create_scanner_pass()
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])
        scan_url = reverse('ticketing:scanner_pass_scan', args=[scanner_pass.token])
        self.client.post(scanner_url, {'pin': '1234'})

        first = self.client.post(scan_url, {'code': ticket.code})
        duplicate = self.client.post(scan_url, {'code': ticket.code})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)

        other_event = Event.objects.create(
            owner=self.user,
            title='Other Ticketed Event',
            kind=Event.Kind.TICKETED_EVENT,
            start_at=timezone.now() + timedelta(days=6),
            end_at=timezone.now() + timedelta(days=6, hours=3),
        )
        other_type = TicketType.objects.create(
            event=other_event,
            name='Other',
            price=Decimal('10.00'),
            quantity_available=10,
            sale_start_at=timezone.now() - timedelta(hours=1),
            sale_end_at=timezone.now() + timedelta(days=1),
        )
        other_purchase = create_ticket_purchase(
            ticket_type=other_type,
            quantity=1,
            buyer_email='other@example.com',
        )
        other_purchase.status = TicketPurchase.Status.PAID
        other_purchase.save(update_fields=['status'])
        other_ticket = Ticket.objects.create(purchase=other_purchase, ticket_type=other_type, event=other_event)
        wrong_event = self.client.post(scan_url, {'code': other_ticket.code})
        self.assertEqual(wrong_event.status_code, 400)
        self.assertNotIn('buyer_name', wrong_event.json())

        scanner_pass.status = TicketScannerPass.Status.REVOKED
        scanner_pass.revoked_at = timezone.now()
        scanner_pass.save(update_fields=['status', 'revoked_at'])
        inactive = self.client.post(scan_url, {'code': other_ticket.code})
        self.assertEqual(inactive.status_code, 403)

    def test_expired_scanner_pass_and_staff_dashboard_access_are_blocked(self):
        scanner_pass = self.create_scanner_pass(expires_at=timezone.now() - timedelta(minutes=1))
        scanner_url = reverse('ticketing:scanner_pass', args=[scanner_pass.token])

        response = self.client.post(scanner_url, {'pin': '1234'})
        self.assertEqual(response.status_code, 403)

        dashboard_response = self.client.get(reverse('dashboard:ticket_scanner_passes', args=[self.event.slug]))
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertIn('/accounts/login/', dashboard_response['Location'])

    def test_ticketed_event_end_time_update_syncs_scanner_pass_expiry(self):
        self.client.force_login(self.user)
        scanner_pass = self.create_scanner_pass()
        new_start = (timezone.now() + timedelta(days=5)).replace(second=0, microsecond=0)
        new_end = new_start + timedelta(hours=6)

        response = self.client.post(
            reverse('dashboard:ticketed_event_edit', args=[self.event.slug]),
            {
                'title': self.event.title,
                'description': self.event.description,
                'currency': self.event.currency,
                'start_at': timezone.localtime(new_start).strftime('%Y-%m-%dT%H:%M'),
                'end_at': timezone.localtime(new_end).strftime('%Y-%m-%dT%H:%M'),
                'is_public': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        scanner_pass.refresh_from_db()
        self.assertEqual(scanner_pass.expires_at, scanner_pass_default_expiry(self.event))

    def test_non_charge_success_without_event_name_does_not_type_error(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_email='buyer@example.com',
        )

        result = handle_ticket_paystack_webhook(
            {
                'data': {
                    'reference': purchase.gateway_reference,
                    'status': 'pending',
                }
            }
        )

        self.assertEqual(result.gateway_status, 'pending')

    def test_public_ticketed_event_page_shows_ticket_form(self):
        self.event.publish()

        response = self.client.get(reverse('events:public_detail', args=[self.event.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Get tickets')
        self.assertContains(response, self.ticket_type.name)

    def test_wrong_event_check_in_does_not_create_database_record(self):
        other_event = Event.objects.create(
            owner=self.user,
            title='Other Ticketed Event',
            kind=Event.Kind.TICKETED_EVENT,
            start_at=timezone.now() + timedelta(days=6),
            end_at=timezone.now() + timedelta(days=6, hours=3),
        )
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name='Ama Buyer',
            buyer_email='ama@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        ticket = Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        initial_count = TicketCheckIn.objects.count()
        result = check_in_ticket(event=other_event, code=ticket.code, user=self.staff)

        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], 'This ticket belongs to a different event.')
        self.assertEqual(TicketCheckIn.objects.count(), initial_count)

    def test_provisional_sync_retains_custom_timestamp(self):
        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name='Ama Buyer',
            buyer_email='ama@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        ticket = Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        offline_time = timezone.now() - timedelta(hours=2)
        from .services import sync_provisional_entry
        result = sync_provisional_entry(
            event=self.event,
            attempt={
                'client_attempt_id': 'attempt-offline-time-test',
                'ticket_code': ticket.code,
                'offline_at': offline_time.isoformat(),
                'device_id': 'device-offline-test',
            },
            user=self.staff,
        )

        self.assertEqual(result['status'], TicketProvisionalEntry.Status.CONFIRMED)
        checkin = TicketCheckIn.objects.get(pk=result['ticket_status'] and TicketProvisionalEntry.objects.get(client_attempt_id='attempt-offline-time-test').final_checkin_id)
        self.assertAlmostEqual(checkin.scanned_at, offline_time, delta=timedelta(seconds=2))

    def test_sold_out_ticket_returns_correct_message(self):
        self.ticket_type.quantity_available = 1
        self.ticket_type.save(update_fields=['quantity_available'])

        purchase = create_ticket_purchase(
            ticket_type=self.ticket_type,
            quantity=1,
            buyer_name='Ama Buyer',
            buyer_email='ama@example.com',
        )
        purchase.status = TicketPurchase.Status.PAID
        purchase.save(update_fields=['status'])
        Ticket.objects.create(
            purchase=purchase,
            ticket_type=self.ticket_type,
            event=self.event,
        )

        allowed, reason = self.ticket_type.can_purchase(1)
        self.assertFalse(allowed)
        self.assertEqual(reason, 'Not enough tickets are available for this ticket type.')

    def test_launcher_queryset_includes_competition_events(self):
        self.client.force_login(self.user)
        comp_event = Event.objects.create(
            owner=self.user,
            title='Vootely Competition',
            kind=Event.Kind.PAID_COMPETITION,
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=3),
            status=Event.Status.DRAFT,
        )

        from .views import DashboardTicketCheckInLaunchView, OrganizerTicketEventMixin
        view = DashboardTicketCheckInLaunchView()
        view.request = self.client.get(reverse('dashboard:ticket_check_in_launch')).wsgi_request
        queryset = view.get_queryset()
        self.assertIn(comp_event, queryset)
