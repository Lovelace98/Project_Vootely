import csv
import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from payments.services import amount_to_minor_units

from .models import (
    Ballot,
    ElectionAuditLog,
    ElectionCandidate,
    ElectionCredential,
    ElectionInvoice,
    ElectionPosition,
    ElectionVoter,
    OrganizerPaymentAttempt,
)
from .services import (
    calculate_graduated_price,
    cast_ballot,
    generate_invoice,
    get_default_pricing_plan,
    handle_organizer_paystack_webhook,
    has_paid_for_current_roster,
    import_roster,
    initialize_organizer_paystack_transaction,
    issue_credentials,
    open_election,
    token_hash,
)


class SecureElectionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organizer = user_model.objects.create_user(
            email='organizer@example.com',
            password='strong-pass-123',
        )
        now = timezone.now()
        self.event = Event.objects.create(
            owner=self.organizer,
            title='SRC Election',
            description='Secure campus election',
            kind=Event.Kind.SECURE_ELECTION,
            currency='GHS',
            start_at=now - timedelta(minutes=5),
            end_at=now + timedelta(hours=2),
            is_public=True,
        )

    def roster_file(self, count, *, duplicate=False, invalid_contact=False):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['external_id', 'name', 'email', 'phone'])
        writer.writeheader()
        for index in range(1, count + 1):
            external_id = 'V001' if duplicate and index == 2 else f'V{index:03d}'
            writer.writerow(
                {
                    'external_id': external_id,
                    'name': f'Voter {index}',
                    'email': 'bad-email' if invalid_contact and index == 1 else f'voter{index}@example.com',
                    'phone': 'not-a-phone' if invalid_contact and index == 1 else '0241234567',
                }
            )
        return SimpleUploadedFile('roster.csv', output.getvalue().encode('utf-8'), content_type='text/csv')

    def add_ballot_setup(self):
        position = ElectionPosition.objects.create(event=self.event, title='President')
        candidate = ElectionCandidate.objects.create(event=self.event, position=position, name='Ama Mensah')
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])
        export = issue_credentials(self.event, actor=self.organizer, email=False)
        open_election(self.event, actor=self.organizer)
        return position, candidate, export.rows[0]['token']

    def test_pricing_uses_graduated_tiers_and_minimum_fee(self):
        plan = get_default_pricing_plan()

        self.assertEqual(calculate_graduated_price(10, plan=plan)[0], Decimal('150.00'))
        self.assertEqual(calculate_graduated_price(50, plan=plan)[0], Decimal('250.00'))
        self.assertEqual(calculate_graduated_price(120, plan=plan)[0], Decimal('440.00'))
        self.assertEqual(calculate_graduated_price(653, plan=plan)[0], Decimal('1241.25'))

    def test_top_up_invoice_charges_only_outstanding_difference(self):
        import_roster(self.event, self.roster_file(50), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()

        import_roster(self.event, self.roster_file(120), actor=self.organizer)
        top_up = generate_invoice(self.event, actor=self.organizer)

        self.assertTrue(top_up.is_top_up)
        self.assertEqual(top_up.amount, Decimal('190.00'))

    def test_open_requires_roster_paid_invoice_and_credentials(self):
        position = ElectionPosition.objects.create(event=self.event, title='President')
        ElectionCandidate.objects.create(event=self.event, position=position, name='Ama Mensah')

        with self.assertRaises(ValidationError):
            open_election(self.event, actor=self.organizer)

        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        with self.assertRaises(ValidationError):
            open_election(self.event, actor=self.organizer)

        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            open_election(self.event, actor=self.organizer)

        issue_credentials(self.event, actor=self.organizer, email=False)
        open_election(self.event, actor=self.organizer)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OPEN)

    def test_dashboard_position_create_shows_duplicate_title_error(self):
        ElectionPosition.objects.create(event=self.event, title='President')
        self.client.login(email='organizer@example.com', password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:election_positions', args=[self.event.slug]),
            data={
                'title': 'President',
                'max_choices': 1,
                'display_order': 0,
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A position with this title already exists for this election.')

    def test_dashboard_candidate_create_shows_duplicate_name_error(self):
        position = ElectionPosition.objects.create(event=self.event, title='President')
        ElectionCandidate.objects.create(event=self.event, position=position, name='Ama Mensah')
        self.client.login(email='organizer@example.com', password='strong-pass-123')

        response = self.client.post(
            reverse('dashboard:election_candidates', args=[self.event.slug]),
            data={
                'position': position.pk,
                'name': 'Ama Mensah',
                'bio': 'Duplicate',
                'email': 'ama2@example.com',
                'phone': '0240000000',
                'display_order': 0,
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A candidate with this name already exists for the selected position.')

    def test_roster_import_rejects_duplicate_ids_and_keeps_invalid_contacts_as_warnings(self):
        with self.assertRaises(ValidationError):
            import_roster(self.event, self.roster_file(2, duplicate=True), actor=self.organizer)

        voters = import_roster(self.event, self.roster_file(1, invalid_contact=True), actor=self.organizer)

        self.assertEqual(len(voters), 1)
        voter = ElectionVoter.objects.get(event=self.event)
        self.assertEqual(voter.email, '')
        self.assertEqual(voter.phone, '')
        self.assertIn('invalid email ignored', voter.metadata['warnings'])

    def test_credentials_store_hashes_reissue_and_do_not_reissue_used_credentials(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])

        first_export = issue_credentials(self.event, actor=self.organizer, email=False)
        first_token = first_export.rows[0]['token']
        self.assertFalse(ElectionCredential.objects.filter(token_hash=first_token).exists())
        self.assertTrue(ElectionCredential.objects.filter(token_hash=token_hash(first_token)).exists())

        second_export = issue_credentials(self.event, actor=self.organizer, email=False)
        self.assertEqual(second_export.row_count, 0)
        self.assertFalse(ElectionCredential.objects.filter(status=ElectionCredential.Status.REISSUED).exists())

        credential = ElectionCredential.objects.get(status=ElectionCredential.Status.ISSUED)
        credential.status = ElectionCredential.Status.USED
        credential.save(update_fields=['status'])
        third_export = issue_credentials(self.event, actor=self.organizer, email=False)
        self.assertEqual(third_export.row_count, 0)

    @override_settings(PUBLIC_APP_URL='https://vote.vootely.com')
    def test_issued_credential_export_uses_public_app_url(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])

        export = issue_credentials(self.event, actor=self.organizer, email=False)

        self.assertTrue(export.rows[0]['vote_url'].startswith('https://vote.vootely.com/elections/'))

    def test_valid_voter_casts_once_and_ballot_has_no_voter_foreign_key(self):
        position, candidate, token = self.add_ballot_setup()

        ballot = cast_ballot(self.event, token, {str(position.id): str(candidate.id)})

        self.assertEqual(Ballot.objects.count(), 1)
        self.assertEqual(ballot.selections.get().candidate, candidate)
        self.assertNotIn('voter', {field.name for field in Ballot._meta.fields})
        self.assertTrue(ballot.receipt.code)

        with self.assertRaises(ValidationError):
            cast_ballot(self.event, token, {str(position.id): str(candidate.id)})

    def test_tally_snapshot_and_audit_log_are_generated(self):
        position, candidate, token = self.add_ballot_setup()
        cast_ballot(self.event, token, {str(position.id): str(candidate.id)})
        self.event.status = Event.Status.CLOSED
        self.event.save(update_fields=['status'])

        from .services import generate_tally

        snapshot = generate_tally(self.event, actor=self.organizer, publish=True)

        self.assertEqual(snapshot.ballot_count, 1)
        self.assertEqual(snapshot.totals['positions'][0]['candidates'][0]['votes'], 1)
        self.assertTrue(ElectionAuditLog.objects.filter(event=self.event, action='tally_generated').exists())

    @override_settings(PAYSTACK_SECRET_KEY='test-secret')
    @patch('elections.services.requests.post')
    def test_organizer_invoice_paystack_initialization_creates_checkout_url(self, mocked_post):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        attempt = OrganizerPaymentAttempt.objects.create(
            event=self.event,
            invoice=invoice,
            owner=self.organizer,
            amount=invoice.amount,
            currency=invoice.currency,
            payer_email=self.organizer.email,
            gateway_reference='org-ref',
        )
        mocked_response = Mock()
        mocked_response.json.return_value = {
            'status': True,
            'data': {'access_code': 'access', 'authorization_url': 'https://checkout.example/pay'},
        }
        mocked_response.raise_for_status.return_value = None
        mocked_post.return_value = mocked_response

        payload = initialize_organizer_paystack_transaction(attempt)

        self.assertEqual(payload['data']['authorization_url'], 'https://checkout.example/pay')
        sent_payload = mocked_post.call_args.kwargs['json']
        self.assertEqual(sent_payload['metadata']['payment_type'], 'secure_election_invoice')

    @override_settings(PAYSTACK_SECRET_KEY='test-secret')
    @patch('elections.views.initialize_organizer_paystack_transaction')
    def test_election_invoice_pay_returns_json_for_ajax_request(self, mocked_initialize):
        self.client.login(email='organizer@example.com', password='strong-pass-123')
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        mocked_initialize.return_value = {
            'status': True,
            'data': {'access_code': 'ajax-access-999', 'authorization_url': 'https://checkout.example/pay-ajax'},
        }

        response = self.client.post(
            reverse('dashboard:election_invoice', args=[self.event.slug]),
            data={'action': 'pay', 'invoice_id': invoice.id},
            headers={'Accept': 'application/json'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['access_code'], 'ajax-access-999')
        self.assertEqual(data['amount'], float(invoice.amount))

    def test_organizer_invoice_webhook_is_idempotent_and_unlocks_payment(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        attempt = OrganizerPaymentAttempt.objects.create(
            event=self.event,
            invoice=invoice,
            owner=self.organizer,
            amount=invoice.amount,
            currency=invoice.currency,
            payer_email=self.organizer.email,
            gateway_reference='org-paid-ref',
            status=OrganizerPaymentAttempt.Status.PENDING,
        )
        payload = {
            'event': 'charge.success',
            'data': {
                'reference': attempt.gateway_reference,
                'status': 'success',
                'amount': amount_to_minor_units(attempt.amount),
                'currency': attempt.currency,
            },
        }

        handle_organizer_paystack_webhook(payload)
        handle_organizer_paystack_webhook(payload)
        invoice.refresh_from_db()
        self.event.refresh_from_db()

        self.assertEqual(invoice.status, ElectionInvoice.Status.PAID)
        self.assertTrue(has_paid_for_current_roster(self.event))
        self.assertEqual(self.event.status, Event.Status.PAID)

    def test_failed_organizer_webhook_leaves_election_blocked(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        attempt = OrganizerPaymentAttempt.objects.create(
            event=self.event,
            invoice=invoice,
            owner=self.organizer,
            amount=invoice.amount,
            currency=invoice.currency,
            payer_email=self.organizer.email,
            gateway_reference='org-failed-ref',
            status=OrganizerPaymentAttempt.Status.PENDING,
        )

        handle_organizer_paystack_webhook(
            {
                'event': 'charge.failed',
                'data': {
                    'reference': attempt.gateway_reference,
                    'status': 'failed',
                    'amount': amount_to_minor_units(attempt.amount),
                    'currency': attempt.currency,
                },
            }
        )
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, ElectionInvoice.Status.FAILED)
        self.assertFalse(has_paid_for_current_roster(self.event))

    def test_public_receipt_verification_does_not_show_candidate_choice(self):
        position, candidate, token = self.add_ballot_setup()
        ballot = cast_ballot(self.event, token, {str(position.id): str(candidate.id)})

        response = self.client.get(reverse('elections:receipt', args=[self.event.slug, ballot.receipt.code]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ballot.receipt.code)
        self.assertNotContains(response, candidate.name)

    def test_roster_locked_cannot_be_changed(self):
        # Initial roster upload
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        
        # Manually lock roster
        self.event.status = Event.Status.ROSTER_LOCKED
        self.event.save(update_fields=['status'])

        # Attempt to upload again should fail
        with self.assertRaises(ValidationError) as ctx:
            import_roster(self.event, self.roster_file(1), actor=self.organizer)
        self.assertIn('Roster cannot be changed once paid, locked, or credentials have been issued', str(ctx.exception))

    def test_paid_roster_can_only_append_unchanged_voters_for_top_up(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])

        changed = io.StringIO()
        writer = csv.DictWriter(changed, fieldnames=['external_id', 'name', 'email', 'phone'])
        writer.writeheader()
        writer.writerow({'external_id': 'V001', 'name': 'Changed Name', 'email': 'voter1@example.com', 'phone': '0241234567'})
        with self.assertRaises(ValidationError):
            import_roster(
                self.event,
                SimpleUploadedFile('changed.csv', changed.getvalue().encode('utf-8')),
                actor=self.organizer,
            )

        appended = import_roster(self.event, self.roster_file(2), actor=self.organizer)
        self.assertEqual(len(appended), 1)
        self.assertEqual(ElectionVoter.objects.filter(event=self.event).count(), 2)

    def test_bulk_credential_issue_does_not_reissue_existing_unused_credentials(self):
        import_roster(self.event, self.roster_file(2), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])

        first_export = issue_credentials(self.event, actor=self.organizer, email=False)
        second_export = issue_credentials(self.event, actor=self.organizer, email=False)

        self.assertEqual(first_export.row_count, 2)
        self.assertEqual(second_export.row_count, 0)
        self.assertEqual(
            ElectionCredential.objects.filter(status=ElectionCredential.Status.ISSUED).count(),
            2,
        )

    def test_bulk_credential_issue_rejects_open_election(self):
        self.add_ballot_setup()

        with self.assertRaises(ValidationError):
            issue_credentials(self.event, actor=self.organizer, email=False)

    def test_open_requires_public_election(self):
        position = ElectionPosition.objects.create(event=self.event, title='President')
        ElectionCandidate.objects.create(event=self.event, position=position, name='Ama Mensah')
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.is_public = False
        self.event.save(update_fields=['status', 'is_public'])
        issue_credentials(self.event, actor=self.organizer, email=False)

        with self.assertRaises(ValidationError) as ctx:
            open_election(self.event, actor=self.organizer)
        self.assertIn('Make the election public', str(ctx.exception))

    def test_created_credential_cannot_cast_ballot(self):
        position = ElectionPosition.objects.create(event=self.event, title='President')
        candidate = ElectionCandidate.objects.create(event=self.event, position=position, name='Ama Mensah')
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        voter = ElectionVoter.objects.get(event=self.event)
        raw_token = 'manual-created-token'
        ElectionCredential.objects.create(
            event=self.event,
            voter=voter,
            token_hash=token_hash(raw_token),
            status=ElectionCredential.Status.CREATED,
        )
        self.event.status = Event.Status.OPEN
        self.event.save(update_fields=['status'])

        with self.assertRaises(ValidationError):
            cast_ballot(self.event, raw_token, {str(position.id): str(candidate.id)})

    def test_setup_cannot_be_changed_after_credentials_issued_via_dashboard(self):
        self.client.login(email='organizer@example.com', password='strong-pass-123')
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])
        issue_credentials(self.event, actor=self.organizer, email=False)

        response = self.client.post(
            reverse('dashboard:election_positions', args=[self.event.slug]),
            data={'title': 'Late Position', 'max_choices': 1, 'display_order': 0, 'is_active': 'on'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ElectionPosition.objects.filter(event=self.event, title='Late Position').exists())

    def test_failed_webhook_after_paid_attempt_does_not_downgrade_invoice(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        paid_attempt = OrganizerPaymentAttempt.objects.create(
            event=self.event,
            invoice=invoice,
            owner=self.organizer,
            amount=invoice.amount,
            currency=invoice.currency,
            payer_email=self.organizer.email,
            gateway_reference='org-paid-first',
            status=OrganizerPaymentAttempt.Status.PENDING,
        )
        failed_attempt = OrganizerPaymentAttempt.objects.create(
            event=self.event,
            invoice=invoice,
            owner=self.organizer,
            amount=invoice.amount,
            currency=invoice.currency,
            payer_email=self.organizer.email,
            gateway_reference='org-failed-second',
            status=OrganizerPaymentAttempt.Status.PENDING,
        )

        handle_organizer_paystack_webhook(
            {
                'event': 'charge.success',
                'data': {
                    'reference': paid_attempt.gateway_reference,
                    'status': 'success',
                    'amount': amount_to_minor_units(paid_attempt.amount),
                    'currency': paid_attempt.currency,
                },
            }
        )
        handle_organizer_paystack_webhook(
            {
                'event': 'charge.failed',
                'data': {
                    'reference': failed_attempt.gateway_reference,
                    'status': 'failed',
                    'amount': amount_to_minor_units(failed_attempt.amount),
                    'currency': failed_attempt.currency,
                },
            }
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, ElectionInvoice.Status.PAID)

    def test_abstain_ballot_casting_and_tally(self):
        # Enable abstain in config
        from .models import ElectionConfig
        config, _ = ElectionConfig.objects.get_or_create(event=self.event)
        config.allow_abstain = True
        config.save(update_fields=['allow_abstain'])

        position, candidate, token = self.add_ballot_setup()
        
        # Cast ballot with empty/abstain selection
        ballot = cast_ballot(self.event, token, {str(position.id): 'abstain'})
        
        self.assertEqual(Ballot.objects.count(), 1)
        selection = ballot.selections.get()
        self.assertIsNone(selection.candidate)
        
        # Verify build_tally records the abstention
        from .services import build_tally
        totals = build_tally(self.event)
        self.assertEqual(totals[0]['abstentions'], 1)
        self.assertEqual(totals[0]['candidates'][0]['votes'], 0)

    def test_multi_choice_max_choices_limit_validation(self):
        position = ElectionPosition.objects.create(event=self.event, title='Committee', max_choices=2)
        cand1 = ElectionCandidate.objects.create(event=self.event, position=position, name='Candidate A')
        cand2 = ElectionCandidate.objects.create(event=self.event, position=position, name='Candidate B')
        cand3 = ElectionCandidate.objects.create(event=self.event, position=position, name='Candidate C')

        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice = generate_invoice(self.event, actor=self.organizer)
        invoice.mark_paid()
        self.event.status = Event.Status.PAID
        self.event.save(update_fields=['status'])
        export = issue_credentials(self.event, actor=self.organizer, email=False)
        token = export.rows[0]['token']
        open_election(self.event, actor=self.organizer)

        # Cast with 2 candidates (valid)
        ballot = cast_ballot(self.event, token, {str(position.id): [str(cand1.id), str(cand2.id)]})
        self.assertEqual(ballot.selections.count(), 2)

        # Re-allow credential to vote (reset status for testing)
        cred = ElectionCredential.objects.get(voter__external_id='V001')
        cred.status = ElectionCredential.Status.ISSUED
        cred.save(update_fields=['status'])

        # Cast with 3 candidates (invalid - exceeds max_choices)
        with self.assertRaises(ValidationError) as ctx:
            cast_ballot(self.event, token, {str(position.id): [str(cand1.id), str(cand2.id), str(cand3.id)]})
        self.assertIn('You can select at most 2 candidates', str(ctx.exception))

    def test_spoofed_candidate_id_raises_validation_error(self):
        position, candidate, token = self.add_ballot_setup()
        
        # Cast with invalid candidate ID (9999)
        with self.assertRaises(ValidationError) as ctx:
            cast_ballot(self.event, token, {str(position.id): '9999'})
        self.assertIn('Invalid candidate selected', str(ctx.exception))

    def test_position_and_candidate_save_delete_blocked_after_credentials_issued(self):
        position, candidate, token = self.add_ballot_setup()
        # Roster is locked, credentials issued, and election opened in add_ballot_setup.
        # This puts event status into Event.Status.OPEN, which is in SETUP_MUTATION_BLOCKED_STATUSES.
        
        # Try to modify candidate name
        candidate.name = "Mutated Candidate"
        with self.assertRaises(ValidationError):
            candidate.save()

        # Try to delete candidate
        with self.assertRaises(ValidationError):
            candidate.delete()

        # Try to delete position
        with self.assertRaises(ValidationError):
            position.delete()

        # Try to save new candidate
        new_cand = ElectionCandidate(event=self.event, position=position, name="Unauthorized New")
        with self.assertRaises(ValidationError):
            new_cand.save()

    def test_generate_invoice_idempotence(self):
        import_roster(self.event, self.roster_file(1), actor=self.organizer)
        invoice1 = generate_invoice(self.event, actor=self.organizer)
        invoice2 = generate_invoice(self.event, actor=self.organizer)
        
        # Verify both calls returned the identical invoice
        self.assertEqual(invoice1.pk, invoice2.pk)

    def test_invalid_lifecycle_actions_via_post(self):
        self.client.login(email='organizer@example.com', password='strong-pass-123')
        
        # Initially, event is in DRAFT
        self.assertEqual(self.event.status, Event.Status.DRAFT)

        # POST invalid 'close' action directly while in DRAFT
        response1 = self.client.post(
            reverse('dashboard:election_action', args=[self.event.slug, 'close']),
            follow=True,
        )
        self.assertEqual(self.event.status, Event.Status.DRAFT)

        # POST invalid 'certify' action directly while in DRAFT
        response2 = self.client.post(
            reverse('dashboard:election_action', args=[self.event.slug, 'certify']),
            follow=True,
        )
        self.assertEqual(self.event.status, Event.Status.DRAFT)

    def test_publish_election_results_sends_notifications(self):
        from .services import publish_election_results, generate_tally
        from notifications.models import Notification

        position, candidate, token = self.add_ballot_setup()
        
        # Bypass setup block by temporarily setting to DRAFT
        self.event.status = Event.Status.DRAFT
        self.event.save(update_fields=['status'])

        candidate.email = 'candidate@example.com'
        candidate.phone = '0241112223'
        candidate.save()

        voter = ElectionVoter.objects.get(event=self.event)
        voter.email = 'voter@example.com'
        voter.phone = '0242223334'
        voter.save()

        # Restore status
        self.event.status = Event.Status.OPEN
        self.event.save(update_fields=['status'])

        cast_ballot(self.event, token, {str(position.id): str(candidate.id)})
        
        self.event.status = Event.Status.CLOSED
        self.event.save(update_fields=['status'])

        generate_tally(self.event, actor=self.organizer)
        self.event.status = Event.Status.TALLIED
        self.event.save(update_fields=['status'])

        # Now call publish_election_results
        with patch('notifications.tasks.send_notification.delay') as mocked_delay:
            publish_election_results(self.event, actor=self.organizer)

        # Assert notifications exist
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.CANDIDATE_ELECTION_CLOSED,
                recipient_email='candidate@example.com',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.CANDIDATE_ELECTION_CLOSED,
                recipient_phone='+233241112223',  # Normalized phone
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.VOTER_ELECTION_CLOSED,
                recipient_email='voter@example.com',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                event_type=Notification.EventType.VOTER_ELECTION_CLOSED,
                recipient_phone='+233242223334',  # Normalized phone
            ).exists()
        )
