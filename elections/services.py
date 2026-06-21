import csv
import hashlib
import io
import secrets
from decimal import Decimal

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.performance import build_tally_fast
from notifications.phone import normalize_phone_number
from payments.services import amount_to_minor_units, generate_reference
from votecentral.public_urls import build_public_url

from .models import (
    Ballot,
    BallotReceipt,
    BallotSelection,
    ElectionAuditLog,
    ElectionCandidate,
    ElectionCredential,
    ElectionCredentialExport,
    ElectionInvoice,
    ElectionPosition,
    ElectionPricingPlan,
    ElectionPricingTier,
    ElectionTallySnapshot,
    ElectionVoter,
    OrganizerPaymentAttempt,
)


DEFAULT_PRICING_TIERS = [
    (1, 50, Decimal('5.00')),
    (51, 100, Decimal('3.00')),
    (101, 300, Decimal('2.00')),
    (301, 700, Decimal('1.25')),
    (701, None, Decimal('1.00')),
]
DEFAULT_MINIMUM_FEE = Decimal('150.00')
DEFAULT_PRICING_PLAN_NAME = 'Self-service secure election'

SETUP_MUTATION_BLOCKED_STATUSES = {
    Event.Status.CREDENTIALS_ISSUED,
    Event.Status.READY,
    Event.Status.OPEN,
    Event.Status.CLOSED,
    Event.Status.TALLIED,
    Event.Status.CERTIFIED,
    Event.Status.ARCHIVED,
}

ROSTER_MUTATION_BLOCKED_STATUSES = {
    Event.Status.ROSTER_LOCKED,
    *SETUP_MUTATION_BLOCKED_STATUSES,
}


def token_hash(value):
    return hashlib.sha256(f'{settings.SECRET_KEY}:{value}'.encode('utf-8')).hexdigest()


def row_hash(*values):
    normalized = '|'.join((value or '').strip().lower() for value in values)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def audit(event, action, *, actor=None, obj=None, metadata=None, request=None):
    return ElectionAuditLog.objects.create(
        event=event,
        actor=actor,
        action=action,
        object_type=obj.__class__.__name__ if obj else '',
        object_id=str(getattr(obj, 'pk', '') or ''),
        metadata=metadata or {},
        ip_address=(request.META.get('REMOTE_ADDR') if request else None) or None,
        user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else ''),
    )


def get_default_pricing_plan():
    plan, _ = ElectionPricingPlan.objects.get_or_create(
        name=DEFAULT_PRICING_PLAN_NAME,
        defaults={
            'currency': 'GHS',
            'minimum_fee': DEFAULT_MINIMUM_FEE,
            'is_active': True,
        },
    )
    if not plan.tiers.exists():
        ElectionPricingTier.objects.bulk_create(
            [
                ElectionPricingTier(plan=plan, start_count=start, end_count=end, rate=rate)
                for start, end, rate in DEFAULT_PRICING_TIERS
            ]
        )
    return plan


def pricing_snapshot(plan):
    tiers = [
        {
            'start_count': tier.start_count,
            'end_count': tier.end_count,
            'rate': str(tier.rate),
        }
        for tier in plan.tiers.order_by('start_count')
    ]
    return {
        'plan_name': plan.name,
        'currency': plan.currency,
        'minimum_fee': str(plan.minimum_fee),
        'tiers': tiers,
    }


def calculate_graduated_price(voter_count, *, snapshot=None, plan=None):
    voter_count = int(voter_count or 0)
    if voter_count <= 0:
        return Decimal('0.00'), []

    if snapshot:
        minimum_fee = Decimal(str(snapshot['minimum_fee']))
        tiers = [
            (
                int(tier['start_count']),
                int(tier['end_count']) if tier.get('end_count') else None,
                Decimal(str(tier['rate'])),
            )
            for tier in snapshot['tiers']
        ]
    else:
        plan = plan or get_default_pricing_plan()
        minimum_fee = plan.minimum_fee
        tiers = [
            (tier.start_count, tier.end_count, tier.rate)
            for tier in plan.tiers.order_by('start_count')
        ]

    total = Decimal('0.00')
    breakdown = []
    for start, end, rate in tiers:
        if voter_count < start:
            continue
        bracket_end = min(voter_count, end) if end else voter_count
        quantity = max(0, bracket_end - start + 1)
        if quantity <= 0:
            continue
        amount = (Decimal(quantity) * rate).quantize(Decimal('0.01'))
        breakdown.append(
            {
                'start_count': start,
                'end_count': end,
                'quantity': quantity,
                'rate': str(rate),
                'amount': str(amount),
            }
        )
        total += amount

    final_amount = max(total, minimum_fee).quantize(Decimal('0.01'))
    return final_amount, breakdown


def eligible_voter_count(event):
    return event.election_voters.filter(status=ElectionVoter.Status.ELIGIBLE).count()


def paid_covered_voter_count(event):
    return (
        event.election_invoices.filter(status=ElectionInvoice.Status.PAID)
        .aggregate(total=Sum('covered_voter_count'))
        .get('total')
        or 0
    )


def paid_invoice_amount(event):
    return (
        event.election_invoices.filter(status=ElectionInvoice.Status.PAID)
        .aggregate(total=Sum('amount_paid'))
        .get('total')
        or Decimal('0.00')
    )


def latest_paid_invoice_snapshot(event):
    invoice = event.election_invoices.filter(status=ElectionInvoice.Status.PAID).order_by('created_at').first()
    return invoice.price_snapshot if invoice else None


def generate_invoice(event, *, actor=None, request=None):
    voter_count = eligible_voter_count(event)
    if voter_count <= 0:
        raise ValidationError('Upload at least one eligible voter before generating an invoice.')

    plan = get_default_pricing_plan()
    snapshot = latest_paid_invoice_snapshot(event) or pricing_snapshot(plan)
    total_price, breakdown = calculate_graduated_price(voter_count, snapshot=snapshot)
    already_paid = paid_invoice_amount(event)
    amount_due = max(Decimal('0.00'), total_price - already_paid).quantize(Decimal('0.01'))
    if amount_due <= 0:
        return event.election_invoices.filter(status=ElectionInvoice.Status.PAID).order_by('-created_at').first()

    pending_invoice = event.election_invoices.filter(
        status=ElectionInvoice.Status.PENDING,
        voter_count=voter_count,
        amount=amount_due,
        currency=snapshot['currency'],
    ).order_by('-created_at').first()
    if pending_invoice:
        return pending_invoice

    invoice = ElectionInvoice.objects.create(
        event=event,
        pricing_plan=plan,
        status=ElectionInvoice.Status.PENDING,
        voter_count=voter_count,
        covered_voter_count=0,
        amount=amount_due,
        currency=snapshot['currency'],
        is_top_up=already_paid > 0,
        price_snapshot={**snapshot, 'breakdown': breakdown, 'total_election_price': str(total_price)},
    )
    event.status = Event.Status.PAYMENT_PENDING
    event.save(update_fields=['status', 'updated_at'])
    audit(event, 'invoice_generated', actor=actor, obj=invoice, request=request)
    return invoice


def validate_roster_row(row, seen_external_ids):
    external_id = (row.get('external_id') or '').strip()
    name = (row.get('name') or '').strip()
    email = (row.get('email') or '').strip()
    phone = (row.get('phone') or '').strip()
    errors = []
    warnings = []

    if not external_id:
        errors.append('external_id is required')
    if not name:
        errors.append('name is required')
    if external_id and external_id in seen_external_ids:
        errors.append('duplicate external_id in file')
    if email:
        try:
            validate_email(email)
        except ValidationError:
            warnings.append('invalid email ignored')
            email = ''
    normalized_phone = ''
    if phone:
        normalized_phone = normalize_phone_number(phone)
        if not normalized_phone:
            warnings.append('invalid phone ignored')

    return {
        'external_id': external_id,
        'name': name,
        'email': email,
        'phone': normalized_phone,
        'errors': errors,
        'warnings': warnings,
    }


@transaction.atomic
def import_roster(event, uploaded_file, *, actor=None, request=None):
    if event.status in ROSTER_MUTATION_BLOCKED_STATUSES:
        raise ValidationError('Roster cannot be changed once paid, locked, or credentials have been issued.')

    content = uploaded_file.read()
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    required_headers = {'external_id', 'name', 'email', 'phone'}
    if not reader.fieldnames or not required_headers.issubset(set(reader.fieldnames)):
        raise ValidationError('CSV must include external_id, name, email, and phone headers.')

    parsed_rows = []
    seen_external_ids = set()
    revoked_external_ids = set(
        event.election_voters.filter(status=ElectionVoter.Status.REVOKED).values_list(
            'external_id',
            flat=True,
        )
    )
    blocking_errors = []
    for index, row in enumerate(reader, start=2):
        parsed = validate_roster_row(row, seen_external_ids)
        if parsed['external_id'] in revoked_external_ids:
            parsed['errors'].append('external_id belongs to a revoked voter')
        if parsed['external_id']:
            seen_external_ids.add(parsed['external_id'])
        if parsed['errors']:
            blocking_errors.append(f'Row {index}: {", ".join(parsed["errors"])}')
        parsed_rows.append(parsed)

    if blocking_errors:
        raise ValidationError(blocking_errors)

    if event.status == Event.Status.PAID:
        existing_voters = {
            voter.external_id: voter
            for voter in event.election_voters.filter(status=ElectionVoter.Status.ELIGIBLE)
        }
        parsed_by_external_id = {row['external_id']: row for row in parsed_rows}
        paid_roster_errors = []
        for external_id, voter in existing_voters.items():
            row = parsed_by_external_id.get(external_id)
            if not row:
                paid_roster_errors.append(f'Existing paid voter {external_id} is missing from the uploaded roster.')
                continue
            candidate_hash = row_hash(row['external_id'], row['name'], row['email'], row['phone'])
            if candidate_hash != voter.row_hash:
                paid_roster_errors.append(f'Existing paid voter {external_id} cannot be changed after payment.')
        if paid_roster_errors:
            raise ValidationError(paid_roster_errors)

        new_rows = [row for row in parsed_rows if row['external_id'] not in existing_voters]
        voters = [
            ElectionVoter(
                event=event,
                external_id=row['external_id'],
                name=row['name'],
                email=row['email'],
                phone=row['phone'],
                row_hash=row_hash(row['external_id'], row['name'], row['email'], row['phone']),
                metadata={'warnings': row['warnings']},
            )
            for row in new_rows
        ]
        ElectionVoter.objects.bulk_create(voters)
    else:
        event.election_voters.all().delete()
        voters = [
            ElectionVoter(
                event=event,
                external_id=row['external_id'],
                name=row['name'],
                email=row['email'],
                phone=row['phone'],
                row_hash=row_hash(row['external_id'], row['name'], row['email'], row['phone']),
                metadata={'warnings': row['warnings']},
            )
            for row in parsed_rows
        ]
        ElectionVoter.objects.bulk_create(voters)

    event.status = Event.Status.ROSTER_UPLOADED
    event.save(update_fields=['status', 'updated_at'])
    audit(
        event,
        'roster_uploaded',
        actor=actor,
        metadata={'row_count': len(voters), 'total_count': eligible_voter_count(event)},
        request=request,
    )
    return voters


def credential_url(event, raw_token):
    path = reverse('elections:vote', args=[event.slug])
    return f'{build_public_url(path)}?token={raw_token}'


def send_credential_notifications(event, voter, raw_token):
    from notifications.services import queue_notification, queue_sms_notification
    from notifications.models import Notification
    
    email_queued = False
    sms_queued = False
    
    if voter.email:
        email_queued = queue_notification(
            channel=Notification.Channel.EMAIL,
            event_type=Notification.EventType.VOTER_CREDENTIALS,
            recipient_email=voter.email,
            recipient_name=voter.name,
            event=event,
            voter=voter,
            credential_token=raw_token,
            vote_url=credential_url(event, raw_token),
            dedupe_parts=(event.pk, voter.pk, 'cred_email'),
        ) is not None
        
    if voter.phone:
        sms_queued = queue_sms_notification(
            event_type=Notification.EventType.VOTER_CREDENTIALS,
            recipient_phone=voter.phone,
            recipient_name=voter.name,
            event=event,
            voter=voter,
            credential_token=raw_token,
            vote_url=credential_url(event, raw_token),
            dedupe_parts=(event.pk, voter.pk, 'cred_sms'),
        ) is not None
        
    return email_queued, sms_queued


@transaction.atomic
def issue_credentials(event, *, actor=None, request=None, email=True):
    if event.status in {
        Event.Status.OPEN,
        Event.Status.CLOSED,
        Event.Status.TALLIED,
        Event.Status.CERTIFIED,
        Event.Status.ARCHIVED,
    }:
        raise ValidationError('Credentials cannot be bulk-issued after the election has opened.')
    voters = event.election_voters.filter(
        status=ElectionVoter.Status.ELIGIBLE
    ).order_by('external_id').prefetch_related('credentials')
    if not voters.exists():
        raise ValidationError('Upload eligible voters before issuing credentials.')
    if not has_paid_for_current_roster(event):
        raise ValidationError('Pay the election invoice before issuing credentials.')

    rows = []
    now = timezone.now()
    for voter in voters:
        voter_creds = list(voter.credentials.all())
        if any(c.status == ElectionCredential.Status.USED for c in voter_creds):
            continue
        existing_active = next(
            (c for c in voter_creds if c.status in {ElectionCredential.Status.ISSUED, ElectionCredential.Status.OPENED}),
            None
        )
        if existing_active:
            continue
        raw_token = secrets.token_urlsafe(24)
        credential = ElectionCredential.objects.create(
            event=event,
            voter=voter,
            token_hash=token_hash(raw_token),
            status=ElectionCredential.Status.ISSUED,
            issued_at=now,
        )
        audit(event, 'credential_issued', actor=actor, obj=credential, request=request)
        
        email_queued, sms_queued = False, False
        if email:
            email_queued, sms_queued = send_credential_notifications(event, voter, raw_token)
            
        rows.append(
            {
                'external_id': voter.external_id,
                'name': voter.name,
                'email': voter.email,
                'phone': voter.phone,
                'token': raw_token,
                'vote_url': credential_url(event, raw_token),
                'email_sent': email_queued or sms_queued,
            }
        )

    if not rows:
        raise ValidationError('All eligible voters already have active credentials.')
    export = ElectionCredentialExport.objects.create(
        event=event,
        generated_by=actor,
        row_count=len(rows),
        rows=rows,
    )
    event.status = Event.Status.CREDENTIALS_ISSUED
    event.save(update_fields=['status', 'updated_at'])
    return export


@transaction.atomic
def reissue_credential(voter, *, actor=None, request=None, email=True):
    event = voter.event
    if event.status in {Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED, Event.Status.ARCHIVED}:
        raise ValidationError('Credentials cannot be reissued after the election has closed.')
    if voter.status != ElectionVoter.Status.ELIGIBLE:
        raise ValidationError('Credentials can only be reissued for eligible voters.')
    if voter.credentials.filter(status=ElectionCredential.Status.USED).exists():
        raise ValidationError('A used credential cannot be reissued.')
    if not has_paid_for_current_roster(event):
        raise ValidationError('Pay the election invoice before reissuing credentials.')

    now = timezone.now()
    previous = voter.credentials.filter(
        status__in=[ElectionCredential.Status.ISSUED, ElectionCredential.Status.OPENED]
    ).first()
    raw_token = secrets.token_urlsafe(24)
    credential = ElectionCredential.objects.create(
        event=event,
        voter=voter,
        token_hash=token_hash(raw_token),
        status=ElectionCredential.Status.ISSUED,
        issued_at=now,
        reissued_from=previous,
    )
    if previous:
        previous.status = ElectionCredential.Status.REISSUED
        previous.revoked_at = now
        previous.save(update_fields=['status', 'revoked_at'])
        
    email_queued, sms_queued = False, False
    if email:
        email_queued, sms_queued = send_credential_notifications(event, voter, raw_token)
        
    audit(event, 'credential_reissued', actor=actor, obj=credential, request=request)
    return credential, {
        'external_id': voter.external_id,
        'name': voter.name,
        'email': voter.email,
        'phone': voter.phone,
        'token': raw_token,
        'vote_url': credential_url(event, raw_token),
        'email_sent': email_queued or sms_queued,
    }


def has_paid_for_current_roster(event):
    count = eligible_voter_count(event)
    if count <= 0:
        return False
    paid_invoices = event.election_invoices.filter(status=ElectionInvoice.Status.PAID)
    if not paid_invoices.exists():
        return False
    snapshot = latest_paid_invoice_snapshot(event)
    total_price, _ = calculate_graduated_price(count, snapshot=snapshot)
    return paid_invoice_amount(event) >= total_price


def can_open_election(event):
    errors = []
    if event.kind != Event.Kind.SECURE_ELECTION:
        errors.append('This is not a secure election.')
    if not event.start_at or not event.end_at or event.end_at <= event.start_at:
        errors.append('Provide a valid voting window.')
    if not event.is_public:
        errors.append('Make the election public before opening it.')
    positions = event.election_positions.filter(is_active=True).prefetch_related('candidates')
    if not positions.exists():
        errors.append('Add at least one active position.')
    for position in positions:
        if not any(c.is_active for c in position.candidates.all()):
            errors.append(f'Add at least one active candidate for {position.title}.')
    if eligible_voter_count(event) <= 0:
        errors.append('Upload at least one eligible voter.')
    if not has_paid_for_current_roster(event):
        errors.append('Pay the election invoice for the current roster.')
    active_credential_voters = event.election_credentials.filter(
        status__in=[
            ElectionCredential.Status.ISSUED,
            ElectionCredential.Status.OPENED,
            ElectionCredential.Status.USED,
        ],
        voter__status=ElectionVoter.Status.ELIGIBLE,
    ).values('voter_id').distinct().count()
    if active_credential_voters < eligible_voter_count(event):
        errors.append('Issue voter credentials for the full roster.')
    return len(errors) == 0, errors


def open_election(event, *, actor=None, request=None):
    if event.status in {Event.Status.OPEN, Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED, Event.Status.ARCHIVED}:
        raise ValidationError('This election cannot be opened from its current state.')
    allowed, errors = can_open_election(event)
    if not allowed:
        raise ValidationError(errors)
    event.status = Event.Status.OPEN
    event.published_at = event.published_at or timezone.now()
    event.save(update_fields=['status', 'published_at', 'updated_at'])
    
    # Wipe sensitive raw credential export tokens once election opens
    event.credential_exports.all().update(rows=[])
    
    audit(event, 'election_opened', actor=actor, request=request)
    return event


def close_election(event, *, actor=None, request=None):
    if event.status != Event.Status.OPEN:
        raise ValidationError('Only an open election can be closed.')
    event.status = Event.Status.CLOSED
    event.save(update_fields=['status', 'updated_at'])
    audit(event, 'election_closed', actor=actor, request=request)
    return event


def resolve_credential(raw_token, *, lock=False):
    queryset = ElectionCredential.objects.select_related('event', 'voter')
    if lock:
        queryset = queryset.select_for_update()
    return queryset.get(token_hash=token_hash(raw_token))


def election_accepts_ballots(event, now=None):
    now = now or timezone.now()
    return (
        event.kind == Event.Kind.SECURE_ELECTION
        and event.status == Event.Status.OPEN
        and event.is_public
        and event.start_at <= now <= event.end_at
    )


@transaction.atomic
def cast_ballot(event, raw_token, selections, *, request=None):
    credential = resolve_credential(raw_token, lock=True)
    if credential.event_id != event.id:
        raise ValidationError('Credential does not belong to this election.')
    if credential.status not in {ElectionCredential.Status.ISSUED, ElectionCredential.Status.OPENED}:
        raise ValidationError('This credential is no longer valid.')
    if not election_accepts_ballots(event):
        raise ValidationError('This election is not accepting ballots right now.')

    positions = list(event.election_positions.filter(is_active=True).prefetch_related('candidates'))
    if not positions:
        raise ValidationError('This election has no active positions.')

    config = getattr(event, 'election_config', None)
    allow_abstain = config.allow_abstain if config else False

    receipt_code = secrets.token_hex(8).upper()
    ballot = Ballot.objects.create(
        event=event,
        receipt_hash=token_hash(receipt_code),
        ip_address=(request.META.get('REMOTE_ADDR') if request else None) or None,
        user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else ''),
    )

    selections_to_create = []

    for position in positions:
        # Retrieve potential list of candidate IDs
        if hasattr(selections, 'getlist'):
            candidate_ids = selections.getlist(str(position.id)) or selections.getlist(position.id)
        else:
            val = selections.get(str(position.id)) or selections.get(position.id)
            if isinstance(val, list):
                candidate_ids = val
            elif val:
                candidate_ids = [val]
            else:
                candidate_ids = []

        # Filter out empty values and explicit 'abstain' values
        candidate_ids = [
            str(cid).strip()
            for cid in candidate_ids
            if cid and str(cid).strip() and str(cid).strip().lower() != 'abstain'
        ]

        # De-duplicate to prevent DB unique constraint violations
        seen_cids = set()
        candidate_ids = [cid for cid in candidate_ids if not (cid in seen_cids or seen_cids.add(cid))]

        if not candidate_ids:
            if not allow_abstain:
                raise ValidationError(f'Select a candidate for {position.title}.')
            selections_to_create.append(BallotSelection(ballot=ballot, position=position, candidate=None))
        else:
            if len(candidate_ids) > position.max_choices:
                raise ValidationError(
                    f'You can select at most {position.max_choices} candidates for {position.title}.'
                )
            candidates_by_id = {str(c.id): c for c in position.candidates.all() if c.is_active}
            for candidate_id in candidate_ids:
                candidate = candidates_by_id.get(candidate_id)
                if candidate is None:
                    raise ValidationError(f'Invalid candidate selected for {position.title}.')
                selections_to_create.append(BallotSelection(ballot=ballot, position=position, candidate=candidate))

    BallotSelection.objects.bulk_create(selections_to_create)

    BallotReceipt.objects.create(ballot=ballot, code=receipt_code, code_hash=token_hash(receipt_code))
    credential.status = ElectionCredential.Status.USED
    credential.used_at = timezone.now()
    credential.save(update_fields=['status', 'used_at'])
    audit(event, 'ballot_cast', obj=ballot, request=request)

    # Queue secure ballot cast confirmation notifications for the voter
    voter = credential.voter
    from notifications.services import queue_notification, queue_sms_notification
    from notifications.models import Notification

    path = reverse('elections:receipt', args=[event.slug, receipt_code])
    receipt_url = build_public_url(path)

    if voter.email:
        queue_notification(
            channel=Notification.Channel.EMAIL,
            event_type=Notification.EventType.VOTER_BALLOT_CAST,
            recipient_email=voter.email,
            recipient_name=voter.name,
            event=event,
            voter=voter,
            credential_token=receipt_code,
            vote_url=receipt_url,
            dedupe_parts=(event.pk, ballot.pk, 'cast_email'),
        )
        
    if voter.phone:
        queue_sms_notification(
            event_type=Notification.EventType.VOTER_BALLOT_CAST,
            recipient_phone=voter.phone,
            recipient_name=voter.name,
            event=event,
            voter=voter,
            credential_token=receipt_code,
            vote_url=receipt_url,
            dedupe_parts=(event.pk, ballot.pk, 'cast_sms'),
        )

    from events.performance import broadcast_election_tally_update
    broadcast_election_tally_update(event.pk)

    return ballot


def build_tally(event):
    return build_tally_fast(event)


def generate_tally(event, *, actor=None, publish=False, request=None):
    if event.status not in {Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED}:
        raise ValidationError('Close the election before tallying results.')
    snapshot = ElectionTallySnapshot.objects.create(
        event=event,
        totals={'positions': build_tally(event)},
        ballot_count=event.ballots.count(),
        generated_by=actor,
        published_at=timezone.now() if publish else None,
    )
    event.status = Event.Status.TALLIED
    event.save(update_fields=['status', 'updated_at'])
    audit(event, 'tally_generated', actor=actor, obj=snapshot, request=request)
    return snapshot


def results_are_public(event):
    config = getattr(event, 'election_config', None)
    if not config:
        return False
    latest = event.tally_snapshots.first()
    return bool(
        latest
        and (
            latest.published_at
            or config.results_visibility == config.ResultsVisibility.AFTER_TALLY
            or (config.results_visibility == config.ResultsVisibility.AFTER_CLOSE and event.status in {Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED})
        )
    )


def initialize_organizer_paystack_transaction(payment_attempt):
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError('PAYSTACK_SECRET_KEY is not configured.')

    email = payment_attempt.payer_email
    if not email or '@' not in email or email.endswith('.local'):
        email = 'demo@vootely.com'

    callback_url = settings.PAYSTACK_CALLBACK_URL
    payload = {
        'reference': payment_attempt.gateway_reference,
        'amount': amount_to_minor_units(payment_attempt.amount),
        'currency': payment_attempt.currency,
        'email': email,
        'callback_url': callback_url,
        'metadata': {
            'payment_type': 'secure_election_invoice',
            'organizer_payment_attempt_id': payment_attempt.pk,
            'invoice_id': payment_attempt.invoice_id,
            'event_slug': payment_attempt.event.slug,
        },
    }
    response = requests.post(
        settings.PAYSTACK_INITIALIZE_URL,
        json=payload,
        headers={
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json',
        },
        timeout=15,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get('status'):
        raise RuntimeError(body.get('message') or 'Paystack initialization failed.')
    return body


def create_organizer_payment_attempt(invoice, *, owner):
    if invoice.status == ElectionInvoice.Status.PAID:
        raise ValidationError('This invoice is already paid.')
    existing = invoice.payment_attempts.filter(
        owner=owner,
        status__in=[
            OrganizerPaymentAttempt.Status.INITIALIZED,
            OrganizerPaymentAttempt.Status.PENDING,
        ],
    ).order_by('-initiated_at').first()
    if existing:
        return existing
    return OrganizerPaymentAttempt.objects.create(
        event=invoice.event,
        invoice=invoice,
        owner=owner,
        amount=invoice.amount,
        currency=invoice.currency,
        payer_email=owner.email,
        gateway_reference=generate_reference(),
        status=OrganizerPaymentAttempt.Status.INITIALIZED,
    )


@transaction.atomic
def record_organizer_paystack_callback(payment_attempt, callback_status=''):
    callback_status = (callback_status or '').strip().lower()
    payment_attempt.callback_received_at = timezone.now()
    if callback_status:
        payment_attempt.gateway_status = callback_status[:32]
    payment_attempt.save(update_fields=['callback_received_at', 'gateway_status'])
    return payment_attempt


def organizer_payment_status_redirect_url(payment_attempt):
    return reverse('dashboard:election_invoice', args=[payment_attempt.event.slug])


def mark_organizer_attempt_unsuccessful(payment_attempt, *, gateway_status='', failure_reason='', cancelled=False, webhook_payload=None):
    if payment_attempt.status == OrganizerPaymentAttempt.Status.PAID:
        return payment_attempt
    payment_attempt.status = OrganizerPaymentAttempt.Status.CANCELLED if cancelled else OrganizerPaymentAttempt.Status.FAILED
    payment_attempt.gateway_status = (gateway_status or payment_attempt.gateway_status)[:32]
    payment_attempt.failure_reason = (failure_reason or payment_attempt.failure_reason)[:255]
    payment_attempt.webhook_payload = webhook_payload or payment_attempt.webhook_payload
    payment_attempt.confirmed_webhook_at = timezone.now()
    payment_attempt.completed_at = payment_attempt.completed_at or timezone.now()
    payment_attempt.save(
        update_fields=[
            'status',
            'gateway_status',
            'failure_reason',
            'webhook_payload',
            'confirmed_webhook_at',
            'completed_at',
        ]
    )
    invoice = payment_attempt.invoice
    if invoice.status != ElectionInvoice.Status.PAID:
        has_pending_or_paid_attempt = invoice.payment_attempts.exclude(pk=payment_attempt.pk).filter(
            status__in=[
                OrganizerPaymentAttempt.Status.PENDING,
                OrganizerPaymentAttempt.Status.PAID,
            ]
        ).exists()
        if not has_pending_or_paid_attempt:
            invoice.status = ElectionInvoice.Status.CANCELLED if cancelled else ElectionInvoice.Status.FAILED
            invoice.save(update_fields=['status', 'updated_at'])
    return payment_attempt


@transaction.atomic
def handle_organizer_paystack_webhook(payload):
    event_name = payload.get('event')
    data = payload.get('data') or {}
    reference = data.get('reference')
    if not reference:
        return None

    attempt = OrganizerPaymentAttempt.objects.select_for_update().select_related(
        'event',
        'invoice',
        'owner',
    ).get(gateway_reference=reference)

    amount_minor = data.get('amount')
    currency = (data.get('currency') or attempt.currency).upper()
    gateway_status = (data.get('status') or '').lower()
    failure_reason = data.get('gateway_response') or data.get('message') or payload.get('message') or ''

    if attempt.status == OrganizerPaymentAttempt.Status.PAID:
        attempt.webhook_payload = payload
        attempt.confirmed_webhook_at = timezone.now()
        attempt.gateway_status = (gateway_status or event_name or attempt.gateway_status)[:32]
        attempt.save(update_fields=['webhook_payload', 'confirmed_webhook_at', 'gateway_status'])
        return attempt

    if event_name != 'charge.success':
        return mark_organizer_attempt_unsuccessful(
            attempt,
            gateway_status=gateway_status or event_name,
            failure_reason=failure_reason,
            cancelled=gateway_status in {'cancelled', 'abandoned'} or 'cancel' in event_name,
            webhook_payload=payload,
        )

    if amount_minor != amount_to_minor_units(attempt.amount) or currency != attempt.currency:
        return mark_organizer_attempt_unsuccessful(
            attempt,
            gateway_status='amount_mismatch',
            failure_reason='The payment amount or currency did not match the expected invoice.',
            webhook_payload=payload,
        )

    attempt.status = OrganizerPaymentAttempt.Status.PAID
    attempt.failure_reason = ''
    attempt.gateway_status = (gateway_status or 'success')[:32]
    attempt.webhook_payload = payload
    attempt.confirmed_webhook_at = timezone.now()
    attempt.completed_at = timezone.now()
    attempt.save(
        update_fields=[
            'status',
            'failure_reason',
            'gateway_status',
            'webhook_payload',
            'confirmed_webhook_at',
            'completed_at',
        ]
    )
    invoice = attempt.invoice
    if invoice.status != ElectionInvoice.Status.PAID:
        invoice.mark_paid(amount=attempt.amount)
    if attempt.event.status not in {Event.Status.OPEN, Event.Status.CLOSED, Event.Status.TALLIED, Event.Status.CERTIFIED, Event.Status.ARCHIVED}:
        attempt.event.status = Event.Status.PAID
        attempt.event.save(update_fields=['status', 'updated_at'])
    audit(attempt.event, 'invoice_paid', actor=attempt.owner, obj=invoice, metadata={'reference': reference})
    return attempt


@transaction.atomic
def lock_election_roster(event, *, actor=None, request=None):
    if event.status in {
        Event.Status.ROSTER_LOCKED,
        Event.Status.CREDENTIALS_ISSUED,
        Event.Status.READY,
        Event.Status.OPEN,
        Event.Status.CLOSED,
        Event.Status.TALLIED,
        Event.Status.CERTIFIED,
        Event.Status.ARCHIVED,
    }:
        return event
    if not has_paid_for_current_roster(event):
        raise ValidationError('Pay the invoice before locking the roster.')
    event.status = Event.Status.ROSTER_LOCKED
    event.save(update_fields=['status', 'updated_at'])
    audit(event, 'roster_locked', actor=actor, request=request)
    return event


@transaction.atomic
def publish_election_results(event, *, actor=None, request=None):
    if event.status not in {Event.Status.TALLIED, Event.Status.CERTIFIED}:
        raise ValidationError('Tally the election before publishing results.')
    snapshot = event.tally_snapshots.first()
    if not snapshot:
        snapshot = generate_tally(event, actor=actor, request=request)
    snapshot.published_at = timezone.now()
    snapshot.save(update_fields=['published_at'])
    audit(event, 'results_published', actor=actor, obj=snapshot, request=request)

    # Dispatch alerts to candidates and eligible voters
    from notifications.services import bulk_queue_notifications
    from notifications.models import Notification
    from elections.models import ElectionVoter

    configs = []

    for candidate in event.election_candidates.filter(is_active=True):
        if candidate.email:
            configs.append({
                'channel': Notification.Channel.EMAIL,
                'event_type': Notification.EventType.CANDIDATE_ELECTION_CLOSED,
                'recipient_email': candidate.email,
                'recipient_name': candidate.name,
                'candidate': candidate,
                'dedupe_parts': (event.pk, candidate.pk, 'results_email'),
            })
        if candidate.phone:
            configs.append({
                'channel': Notification.Channel.SMS,
                'event_type': Notification.EventType.CANDIDATE_ELECTION_CLOSED,
                'recipient_phone': candidate.phone,
                'recipient_name': candidate.name,
                'candidate': candidate,
                'dedupe_parts': (event.pk, candidate.pk, 'results_sms'),
            })

    for voter in event.election_voters.filter(status=ElectionVoter.Status.ELIGIBLE):
        if voter.email:
            configs.append({
                'channel': Notification.Channel.EMAIL,
                'event_type': Notification.EventType.VOTER_ELECTION_CLOSED,
                'recipient_email': voter.email,
                'recipient_name': voter.name,
                'voter': voter,
                'dedupe_parts': (event.pk, voter.pk, 'results_email'),
            })
        if voter.phone:
            configs.append({
                'channel': Notification.Channel.SMS,
                'event_type': Notification.EventType.VOTER_ELECTION_CLOSED,
                'recipient_phone': voter.phone,
                'recipient_name': voter.name,
                'voter': voter,
                'dedupe_parts': (event.pk, voter.pk, 'results_sms'),
            })

    bulk_queue_notifications(configs, event=event)

    return snapshot


@transaction.atomic
def certify_election(event, *, actor=None, request=None):
    if event.status != Event.Status.TALLIED:
        raise ValidationError('Only a tallied election can be certified.')
    event.status = Event.Status.CERTIFIED
    event.save(update_fields=['status', 'updated_at'])
    audit(event, 'election_certified', actor=actor, request=request)
    return event


def ensure_setup_can_change(event):
    if event.status in SETUP_MUTATION_BLOCKED_STATUSES:
        raise ValidationError('Election setup cannot be changed once credentials have been issued or voting has started.')


def revoke_credential(credential, *, actor=None, request=None):
    credential.status = ElectionCredential.Status.REVOKED
    credential.revoked_at = timezone.now()
    credential.save(update_fields=['status', 'revoked_at'])
    audit(credential.event, 'credential_revoked', actor=actor, obj=credential, request=request)
    return credential
