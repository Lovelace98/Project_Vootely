import json
import logging
from decimal import Decimal
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from events.models import Event
from nominees.models import Nominee
from payments.models import PaymentAttempt
from payments.services import generate_reference, charge_momo_stk_push, mark_payment_attempt_unsuccessful
from ticketing.models import TicketType
from ticketing.services import charge_ticket_momo_stk_push, create_ticket_purchase, mark_ticket_purchase_unsuccessful
from .models import USSDSession

logger = logging.getLogger(__name__)


def ussd_response(session_id, user_id, msisdn, message, continue_session=True):
    return JsonResponse({
        'sessionID': session_id,
        'userID': user_id,
        'msisdn': msisdn,
        'message': message,
        'continueSession': continue_session,
    })


def parse_direct_ussd_code(user_data):
    dial_string = (user_data or '').strip()
    prefix = direct_ussd_prefix()
    if not dial_string or not prefix:
        return None
    if not dial_string.startswith(prefix):
        return None
    suffix = dial_string[len(prefix):]
    if suffix.endswith('#'):
        suffix = suffix[:-1]
    if suffix.isdigit():
        return int(suffix)
    return None


def direct_ussd_prefix():
    base_code = getattr(settings, 'USSD_SHORT_CODE', '*920*24#').strip()
    if not base_code:
        return ''
    base_without_hash = base_code[:-1] if base_code.endswith('#') else base_code
    return f'{base_without_hash}*'


def has_direct_ussd_suffix(user_data):
    dial_string = (user_data or '').strip()
    prefix = direct_ussd_prefix()
    return bool(prefix and dial_string.startswith(prefix))


def ticket_types_available_for_ussd(event):
    ticket_types = list(event.ticket_types.filter(is_active=True).order_by('price', 'name').annotate_sold_count())
    return [ticket_type for ticket_type in ticket_types if ticket_type.can_purchase(1)[0]]


def resolve_ticket_event_by_ussd_code(ussd_code):
    if ussd_code is None:
        return None
    event = Event.objects.filter(
        ussd_code=ussd_code,
        kind=Event.Kind.TICKETED_EVENT,
        status=Event.Status.PUBLISHED,
        is_public=True,
    ).first()
    if event and ticket_types_available_for_ussd(event):
        return event
    return None


def resolve_ticket_event_from_user_input(event_code):
    event_code = (event_code or '').strip()
    if not event_code:
        return None
    event = Event.objects.filter(
        slug__iexact=event_code,
        kind=Event.Kind.TICKETED_EVENT,
        status=Event.Status.PUBLISHED,
        is_public=True,
    ).first()
    if event is None:
        event = Event.objects.filter(
            public_code__iexact=event_code,
            kind=Event.Kind.TICKETED_EVENT,
            status=Event.Status.PUBLISHED,
            is_public=True,
        ).first()
    if event is None and event_code.isdigit():
        event = Event.objects.filter(
            ussd_code=int(event_code),
            kind=Event.Kind.TICKETED_EVENT,
            status=Event.Status.PUBLISHED,
            is_public=True,
        ).first()
    if event is None and event_code.isdigit():
        event = Event.objects.filter(
            pk=int(event_code),
            kind=Event.Kind.TICKETED_EVENT,
            status=Event.Status.PUBLISHED,
            is_public=True,
        ).first()
    return event


def render_ticket_event_menu(event):
    return "\n".join([
        event.title,
        f"Venue: {event.venue_display}",
        f"Date: {event.event_date_display}",
        "",
        "1. Buy ticket for self",
        "2. Buy ticket for someone",
        "3. Cancel",
    ])


def render_ticket_type_menu(event):
    ticket_types = ticket_types_available_for_ussd(event)
    menu_lines = [f"Tickets for {event.title}:"]
    for idx, ticket_type in enumerate(ticket_types[:8]):
        menu_lines.append(f"{idx+1}. {ticket_type.name} - GHS {ticket_type.price:.2f}")
    menu_lines.append(f"{min(len(ticket_types), 8)+1}. Cancel")
    return "\n".join(menu_lines)


def normalize_ghana_phone(phone):
    digits = ''.join(character for character in (phone or '') if character.isdigit())
    if digits.startswith('233') and len(digits) == 12:
        return f'+{digits}'
    if digits.startswith('0') and len(digits) == 10:
        return f'+233{digits[1:]}'
    if len(digits) == 9:
        return f'+233{digits}'
    return ''


@csrf_exempt
@require_POST
def arkesel_ussd_callback(request):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'message': 'Invalid JSON', 'continueSession': False}, status=400)

    session_id = payload.get('sessionID')
    user_id = payload.get('userID')
    msisdn = payload.get('msisdn')
    new_session = payload.get('newSession', False)
    user_data = payload.get('userData', '').strip()

    if not session_id or not msisdn:
        return JsonResponse({'message': 'Missing sessionID or msisdn', 'continueSession': False}, status=400)

    welcome_message = "Welcome to Vootely!\n1. Vote for a nominee\n2. Buy Tickets\n3. Cancel"

    # 1. Handle New Session Initiation
    if new_session:
        # Clean up stale sessions older than 1 hour to prevent DB bloat
        expiry_time = timezone.now() - timezone.timedelta(hours=1)
        USSDSession.objects.filter(created_at__lt=expiry_time).delete()

        USSDSession.objects.filter(session_id=session_id).delete()
        session = USSDSession.objects.create(
            session_id=session_id,
            phone_number=msisdn,
            user_id=user_id or '',
            current_state='INITIATE'
        )
        direct_ussd_code = parse_direct_ussd_code(user_data)
        direct_event = resolve_ticket_event_by_ussd_code(direct_ussd_code)
        if direct_event:
            session.event_id = direct_event.id
            session.current_state = 'SELECT_TICKET_PURCHASE_FOR'
            session.save(update_fields=['event_id', 'current_state', 'updated_at'])
            return ussd_response(session_id, user_id, msisdn, render_ticket_event_menu(direct_event), True)
        if direct_ussd_code is not None or has_direct_ussd_suffix(user_data):
            session.delete()
            return ussd_response(
                session_id,
                user_id,
                msisdn,
                "Ticket event not found or unavailable.\nThank you for using Vootely.",
                False,
            )
        return ussd_response(session_id, user_id, msisdn, welcome_message, True)

    # 2. Retrieve existing session
    try:
        session = USSDSession.objects.get(session_id=session_id)
    except USSDSession.DoesNotExist:
        session = USSDSession.objects.create(
            session_id=session_id,
            phone_number=msisdn,
            user_id=user_id or '',
            current_state='INITIATE'
        )
        return ussd_response(session_id, user_id, msisdn, welcome_message, True)

    message = ""
    continue_session = True
    routed_from_init = False

    # 3. Process State Machine
    if session.current_state == 'INITIATE':
        if user_data == '1':
            session.current_state = 'ENTER_NOMINEE_CODE'
            session.save(update_fields=['current_state', 'updated_at'])
            message = "Enter Nominee Code to vote (e.g. K9X2B):"
            routed_from_init = True
        elif user_data == '2':
            session.current_state = 'ENTER_TICKET_EVENT_CODE'
            session.save(update_fields=['current_state', 'updated_at'])
            message = "Enter event code or slug to buy tickets:"
            routed_from_init = True
        elif user_data == '3':
            message = "Thank you for using Vootely."
            continue_session = False
            session.delete()
            routed_from_init = True
        else:
            message = f"Invalid option.\n{welcome_message}"
            routed_from_init = True

    if session.current_state == 'ENTER_NOMINEE_CODE' and not routed_from_init:
        nominee_code = user_data
        try:
            nominee = Nominee.objects.select_related('event').get(code__iexact=nominee_code)
            event = nominee.event
            
            if not event.accepts_votes():
                message = f"Event '{event.title}' is not currently accepting votes.\nThank you."
                continue_session = False
                session.delete()
            else:
                session.nominee_id = nominee.id
                
                # Check for active vote bundles
                bundles = list(event.vote_bundles.filter(is_active=True).order_by('quantity'))
                if bundles:
                    session.current_state = 'SELECT_BUNDLE'
                    session.save()
                    menu_lines = [f"Vote for {nominee.name}:"]
                    for idx, b in enumerate(bundles):
                        menu_lines.append(f"{idx+1}. {b.quantity} votes - GHS {b.price:.2f}")
                    menu_lines.append(f"{len(bundles)+1}. Custom quantity")
                    message = "\n".join(menu_lines)
                else:
                    session.current_state = 'ENTER_VOTES'
                    session.save()
                    vote_price = event.vote_price or 1.00
                    message = f"Vote for {nominee.name}.\nEnter number of votes (GHS {vote_price:.2f} each):"
        except Nominee.DoesNotExist:
            message = "Nominee not found.\nPlease enter a valid Nominee Code (e.g. K9X2B):"

    elif session.current_state == 'ENTER_TICKET_EVENT_CODE' and not routed_from_init:
        event = resolve_ticket_event_from_user_input(user_data)
        if event is None:
            message = "Event not found.\nEnter a valid event code or slug:"
        else:
            ticket_types = ticket_types_available_for_ussd(event)
            if not ticket_types:
                message = f"No tickets are available for {event.title}.\nThank you."
                continue_session = False
                session.delete()
            else:
                session.event_id = event.id
                session.current_state = 'SELECT_TICKET_PURCHASE_FOR'
                session.save()
                message = render_ticket_event_menu(event)

    elif session.current_state == 'SELECT_TICKET_PURCHASE_FOR':
        try:
            event = Event.objects.get(id=session.event_id)
        except Event.DoesNotExist:
            message = "Ticket event not found. Please start again."
            continue_session = False
            session.delete()
        else:
            if user_data == '1':
                session.purchase_for = 'self'
                session.recipient_phone = ''
                session.current_state = 'SELECT_TICKET_TYPE'
                session.save(update_fields=['purchase_for', 'recipient_phone', 'current_state', 'updated_at'])
                message = render_ticket_type_menu(event)
            elif user_data == '2':
                session.purchase_for = 'someone'
                session.recipient_phone = ''
                session.current_state = 'ENTER_RECIPIENT_PHONE'
                session.save(update_fields=['purchase_for', 'recipient_phone', 'current_state', 'updated_at'])
                message = "Enter recipient phone number:"
            elif user_data == '3':
                message = "Ticket purchase cancelled. Thank you for using Vootely."
                continue_session = False
                session.delete()
            else:
                message = f"Invalid option.\n{render_ticket_event_menu(event)}"

    elif session.current_state == 'ENTER_RECIPIENT_PHONE':
        recipient_phone = normalize_ghana_phone(user_data)
        if not recipient_phone:
            message = "Invalid phone number. Enter recipient phone number:"
        else:
            session.recipient_phone = recipient_phone
            session.current_state = 'CONFIRM_RECIPIENT_PHONE'
            session.save(update_fields=['recipient_phone', 'current_state', 'updated_at'])
            message = f"Confirm recipient phone:\n{recipient_phone}\n1. Confirm\n2. Re-enter\n3. Cancel"

    elif session.current_state == 'CONFIRM_RECIPIENT_PHONE':
        if user_data == '1':
            try:
                event = Event.objects.get(id=session.event_id)
            except Event.DoesNotExist:
                message = "Ticket event not found. Please start again."
                continue_session = False
                session.delete()
            else:
                session.current_state = 'SELECT_TICKET_TYPE'
                session.save(update_fields=['current_state', 'updated_at'])
                message = render_ticket_type_menu(event)
        elif user_data == '2':
            session.recipient_phone = ''
            session.current_state = 'ENTER_RECIPIENT_PHONE'
            session.save(update_fields=['recipient_phone', 'current_state', 'updated_at'])
            message = "Enter recipient phone number:"
        elif user_data == '3':
            message = "Ticket purchase cancelled. Thank you for using Vootely."
            continue_session = False
            session.delete()
        else:
            message = f"Invalid option.\nConfirm recipient phone:\n{session.recipient_phone}\n1. Confirm\n2. Re-enter\n3. Cancel"

    elif session.current_state == 'SELECT_TICKET_TYPE':
        try:
            choice = int(user_data)
            event = Event.objects.get(id=session.event_id)
            ticket_types = ticket_types_available_for_ussd(event)
            cancel_choice = min(len(ticket_types), 8) + 1
            if choice == cancel_choice:
                message = "Ticket purchase cancelled. Thank you for using Vootely."
                continue_session = False
                session.delete()
            elif not (1 <= choice <= len(ticket_types[:8])):
                raise ValueError()
            else:
                ticket_type = ticket_types[choice - 1]
                session.ticket_type_id = ticket_type.id
                session.current_state = 'ENTER_TICKET_QUANTITY'
                session.save()
                message = f"Enter quantity for {ticket_type.name} (max {ticket_type.max_per_order}):"
        except (ValueError, Event.DoesNotExist):
            try:
                event = Event.objects.get(id=session.event_id)
                message = f"Invalid option.\n{render_ticket_type_menu(event)}"
            except Event.DoesNotExist:
                message = "Ticket event not found. Please start again."
                continue_session = False
                session.delete()

    elif session.current_state == 'ENTER_TICKET_QUANTITY':
        try:
            quantity = int(user_data)
            ticket_type = TicketType.objects.select_related('event').get(id=session.ticket_type_id)
            allowed, reason = ticket_type.can_purchase(quantity)
            if not allowed:
                message = f"{reason}\nEnter quantity:"
            else:
                base_cost = ticket_type.price * Decimal(quantity)
                buyer_fee = (base_cost * Decimal('0.025')).quantize(Decimal('0.01'))
                total_cost = base_cost + buyer_fee
                session.ticket_quantity = quantity
                session.amount_due = total_cost
                session.current_state = 'CONFIRM_TICKET_PAYMENT'
                session.save()
                message = f"Confirm GHS {total_cost:.2f} for {quantity} {ticket_type.name} ticket(s).\n1. Confirm & Pay\n2. Cancel"
        except (ValueError, TicketType.DoesNotExist):
            message = "Invalid quantity. Enter number of tickets:"

    elif session.current_state == 'SELECT_BUNDLE':
        try:
            choice = int(user_data)
            nominee = Nominee.objects.select_related('event').get(id=session.nominee_id)
            event = nominee.event
            bundles = list(event.vote_bundles.filter(is_active=True).order_by('quantity'))
            
            if 1 <= choice <= len(bundles):
                bundle = bundles[choice - 1]
                session.votes_count = bundle.quantity
                session.amount_due = bundle.price
                session.current_state = 'CONFIRM_PAYMENT'
                session.save()
                message = f"Confirm GHS {bundle.price:.2f} for {bundle.quantity} votes for {nominee.name}.\n1. Confirm & Pay\n2. Cancel"
            elif choice == len(bundles) + 1:
                session.current_state = 'ENTER_CUSTOM_VOTES'
                session.save()
                vote_price = event.vote_price or 1.00
                message = f"Enter number of votes (GHS {vote_price:.2f} each):"
            else:
                raise ValueError()
        except (ValueError, Nominee.DoesNotExist):
            try:
                nominee = Nominee.objects.select_related('event').get(id=session.nominee_id)
            except Nominee.DoesNotExist:
                message = "We could not find that nominee anymore. Please start again."
                continue_session = False
                session.delete()
            else:
                event = nominee.event
                bundles = list(event.vote_bundles.filter(is_active=True).order_by('quantity'))
                menu_lines = ["Invalid option. Select package:"]
                for idx, b in enumerate(bundles):
                    menu_lines.append(f"{idx+1}. {b.quantity} votes - GHS {b.price:.2f}")
                menu_lines.append(f"{len(bundles)+1}. Custom quantity")
                message = "\n".join(menu_lines)

    elif session.current_state == 'ENTER_CUSTOM_VOTES' or session.current_state == 'ENTER_VOTES':
        try:
            votes = int(user_data)
            if votes <= 0:
                raise ValueError()
            
            nominee = Nominee.objects.select_related('event').get(id=session.nominee_id)
            event = nominee.event
            vote_price = event.vote_price or 1.00
            total_cost = votes * vote_price
            
            if total_cost > 10000:
                message = "Amount exceeds MoMo limits (max GHS 10,000.00).\nEnter number of votes:"
            else:
                session.votes_count = votes
                session.amount_due = total_cost
                session.current_state = 'CONFIRM_PAYMENT'
                session.save()
                message = f"Confirm GHS {total_cost:.2f} for {votes} votes for {nominee.name}.\n1. Confirm & Pay\n2. Cancel"
        except (ValueError, Nominee.DoesNotExist):
            message = "Invalid number. Enter number of votes:"

    elif session.current_state == 'CONFIRM_PAYMENT':
        if user_data == '1':
            payment_attempt = None
            try:
                nominee = Nominee.objects.select_related('event').get(id=session.nominee_id)
                event = nominee.event
                
                # 1. Create a PaymentAttempt record in our DB
                ref = generate_reference()
                payment_attempt = PaymentAttempt.objects.create(
                    event=event,
                    nominee=nominee,
                    gateway=PaymentAttempt.Gateway.PAYSTACK,
                    status=PaymentAttempt.Status.INITIALIZED,
                    amount=session.amount_due,
                    currency=event.currency or 'GHS',
                    platform_commission_percent=event.platform_commission_percent,
                    vote_quantity=session.votes_count,
                    voter_phone=session.phone_number,
                    voter_email=f"ussd_{session.phone_number}@vootely.com",
                    voter_name="USSD Voter",
                    gateway_reference=ref
                )
                
                # 2. Trigger Paystack STK Push
                charge_momo_stk_push(payment_attempt)
                
                message = "A mobile money prompt has been sent to your phone. Please enter your MoMo PIN to complete the vote. Thank you!"
            except (OSError, RuntimeError, ValueError) as e:
                logger.exception("Failed to trigger mobile money charge")
                if payment_attempt is not None:
                    mark_payment_attempt_unsuccessful(
                        payment_attempt,
                        gateway_status='ussd_charge_failed',
                        failure_reason='Could not initiate the mobile money prompt.',
                    )
                message = "An error occurred initiating payment prompt. Please try again later."
            
            continue_session = False
            session.delete()
        elif user_data == '2':
            message = "Voting cancelled. Thank you for using Vootely."
            continue_session = False
            session.delete()
        else:
            try:
                nominee = Nominee.objects.get(id=session.nominee_id)
                nominee_name = nominee.name
            except Nominee.DoesNotExist:
                nominee_name = "Nominee"
            message = f"Invalid option.\nConfirm GHS {session.amount_due:.2f} for {session.votes_count} votes for {nominee_name}.\n1. Confirm & Pay\n2. Cancel"
            continue_session = True

    elif session.current_state == 'CONFIRM_TICKET_PAYMENT':
        if user_data == '1':
            purchase = None
            try:
                ticket_type = TicketType.objects.select_related('event').get(id=session.ticket_type_id)
                metadata = {
                    'source': 'ussd',
                    'ussd_session_id': session.session_id,
                    'purchase_for': session.purchase_for or 'self',
                    'payer_phone': session.phone_number,
                }
                if session.purchase_for == 'someone':
                    metadata['recipient_phone'] = session.recipient_phone
                purchase = create_ticket_purchase(
                    ticket_type=ticket_type,
                    quantity=session.ticket_quantity,
                    buyer_name='USSD Buyer' if session.purchase_for != 'someone' else 'USSD Gift Buyer',
                    buyer_email=f"ussd_{session.phone_number}@vootely.com",
                    buyer_phone=session.phone_number,
                    metadata=metadata,
                )
                charge_ticket_momo_stk_push(purchase)
                message = "A mobile money prompt has been sent to your phone. Complete payment to receive your ticket by SMS."
            except (OSError, RuntimeError, ValueError):
                logger.exception("Failed to trigger ticket mobile money charge")
                if purchase is not None:
                    mark_ticket_purchase_unsuccessful(
                        purchase,
                        gateway_status='ussd_charge_failed',
                        failure_reason='Could not initiate the mobile money prompt.',
                    )
                message = "An error occurred initiating ticket payment. Please try again later."
            continue_session = False
            session.delete()
        elif user_data == '2':
            message = "Ticket purchase cancelled. Thank you for using Vootely."
            continue_session = False
            session.delete()
        else:
            message = f"Invalid option.\nConfirm GHS {session.amount_due:.2f} for ticket purchase.\n1. Confirm & Pay\n2. Cancel"
            continue_session = True

    return ussd_response(session_id, user_id, msisdn, message, continue_session)
