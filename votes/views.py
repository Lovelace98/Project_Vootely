import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from nominees.models import Nominee
from payments.models import PaymentAttempt
from payments.services import generate_reference, charge_momo_stk_push
from .models import USSDSession

logger = logging.getLogger(__name__)

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
        return JsonResponse({
            'sessionID': session_id,
            'userID': user_id,
            'msisdn': msisdn,
            'message': "Welcome to Vootely!\nEnter Nominee Code to vote (e.g. K9X2B):",
            'continueSession': True
        })

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
        return JsonResponse({
            'sessionID': session_id,
            'userID': user_id,
            'msisdn': msisdn,
            'message': "Welcome to Vootely!\nEnter Nominee Code to vote (e.g. K9X2B):",
            'continueSession': True
        })

    message = ""
    continue_session = True

    # 3. Process State Machine
    if session.current_state == 'INITIATE':
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
                session.current_state = 'ENTER_VOTES'
                session.save()
                vote_price = event.vote_price or 1.00
                message = f"Vote for {nominee.name}.\nEnter number of votes (GHS {vote_price:.2f} each):"
        except Nominee.DoesNotExist:
            message = "Nominee not found.\nPlease enter a valid Nominee Code (e.g. K9X2B):"

    elif session.current_state == 'ENTER_VOTES':
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
            except Exception as e:
                logger.exception("Failed to trigger mobile money charge")
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

    return JsonResponse({
        'sessionID': session_id,
        'userID': user_id,
        'msisdn': msisdn,
        'message': message,
        'continueSession': continue_session
    })
