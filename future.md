# Future Roadmap: USSD Voting & 4-Digit Nominee Shortcodes

This document provides a highly detailed engineering and implementation guide for adding an offline USSD voting channel to **Vootely**. It outlines the database updates, session handlers, webhook views, and MoMo STK Push payment integrations.

---

## 1. Voter Experience Flow (Shared USSD)

Vootely will use a single **Shared USSD Code** (e.g., `*920*24#`) to support thousands of parallel events at zero incremental telecom cost.

```
Voter dials *920*24#
  │
  ├──► Screen 1: "Welcome to Vootely! Enter Candidate Code (e.g. 5520):"
  │              [Voter enters nominee short_code: 5520]
  │
  ├──► Screen 2: "Vote for Kofi Jamar in National Music Showcase 2026."
  │              "Enter number of votes (GHS 1.00 each):"
  │              [Voter enters count: 10]
  │
  ├──► Screen 3: "Confirm payment of GHS 10.00 to purchase 10 votes for Kofi Jamar."
  │              "1. Confirm & Pay"
  │              "2. Cancel"
  │              [Voter enters: 1]
  │
  └──► Screen 4 (End): "A mobile money prompt has been sent to your phone."
                       "Enter your MoMo PIN to complete your purchase. Thank you!"
```

---

## 2. Model Adjustments: 4-Digit Nominee Shortcodes

Currently, nominees are assigned an 8-character uuid-hex string (e.g. `9AEA6875`) in `nominees/models.py`. While globally unique, typing this hex code on mobile dialpads is error-prone. 

We will introduce a **globally unique 4-digit numeric shortcode** (between `1000` and `9999`) assigned to each nominee while their competition is active.

### Modified Nominee Model (`nominees/models.py`)

```python
# nominees/models.py
import random
from django.db import models
from django.db.models import Q

def generate_short_code():
    """
    Generates a unique 4-digit numeric shortcode between 1000 and 9999
    that is not currently active.
    """
    while True:
        # Generate random 4-digit number
        code = str(random.randint(1000, 9999))
        # Ensure it doesn't clash with any currently active published nominee
        clash = Nominee.objects.filter(
            Q(short_code=code),
            Q(event__status='published')
        ).exists()
        if not clash:
            return code

class Nominee(models.Model):
    # Existing fields
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='nominees')
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, blank=True)
    code = models.CharField(max_length=12, unique=True, default=generate_vote_code, editable=False)
    
    # New Field: 4-Digit Shortcode
    short_code = models.CharField(
        max_length=4,
        unique=True,
        default=generate_short_code,
        db_index=True,
        help_text="4-digit numeric code for offline USSD voting (e.g. 5520)"
    )
    
    # Other existing fields
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='nominees/photos/', blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def resolve_for_event(cls, event, reference):
        """
        Supports resolving nominee via slug, standard uuid code, or new 4-digit shortcode
        """
        return cls.objects.get(
            Q(event=event),
            Q(slug=reference) | Q(code__iexact=reference) | Q(short_code=reference)
        )
```

---

## 3. Database Schema: USSD Session Tracker

Since telecom USSD prompts are stateless, we must store a session tracker in the database to remember the voter's active screen and inputs across HTTP queries.

```python
# votes/models.py
from django.db import models

class USSDSession(models.Model):
    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    phone_number = models.CharField(max_length=20)
    current_state = models.CharField(max_length=50, default='INITIATE')
    
    # Cached inputs
    nominee_id = models.IntegerField(null=True, blank=True)
    votes_count = models.IntegerField(null=True, blank=True)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.phone_number} - {self.current_state}"
```

---

## 4. Webhook Controller (Django)

We set up a Django URL endpoint mapped to the aggregator gateway (e.g., `https://vootely.com/payments/ussd/callback/`).

```python
# votes/views.py
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from nominees.models import Nominee
from .models import USSDSession
import json

@csrf_exempt
def ussd_gateway_callback(request):
    """
    USSD Webhook view mapped to Hubtel, Arkesel, or Africa's Talking.
    """
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)
        
    payload = request.POST if request.POST else json.loads(request.body)
    session_id = payload.get('SessionId')
    phone_number = payload.get('Mobile')
    ussd_type = payload.get('Type') # Initiation, Response, Release
    user_input = payload.get('Message', '').strip()

    # Get or create active session
    session, created = USSDSession.objects.get_or_create(
        session_id=session_id,
        defaults={'phone_number': phone_number, 'current_state': 'INITIATE'}
    )

    if ussd_type == 'Release':
        session.delete()
        return HttpResponse("Session Terminated")

    response_text = ""

    # Menu State Engine
    if session.current_state == 'INITIATE':
        if created or not user_input:
            response_text = "CON Welcome to Vootely!\nEnter Candidate Code to vote (e.g. 5520):"
        else:
            try:
                # Query nominee by their user-friendly 4-digit shortcode
                nominee = Nominee.objects.get(short_code=user_input, event__status='published')
                session.nominee_id = nominee.id
                session.current_state = 'ENTER_VOTES'
                session.save()
                
                vote_price = nominee.event.vote_price or 1.00
                response_text = f"CON Vote for {nominee.name}.\nEnter number of votes (GHS {vote_price:.2f} each):"
            except Nominee.DoesNotExist:
                response_text = "CON Invalid code. Enter Candidate Code (e.g. 5520):"

    elif session.current_state == 'ENTER_VOTES':
        try:
            votes = int(user_input)
            if votes <= 0:
                raise ValueError()
            
            nominee = Nominee.objects.get(id=session.nominee_id)
            vote_price = nominee.event.vote_price or 1.00
            total_cost = votes * vote_price
            
            session.votes_count = votes
            session.amount_due = total_cost
            session.current_state = 'CONFIRM_PAYMENT'
            session.save()
            
            response_text = f"CON Confirm GHS {total_cost:.2f} for {votes} votes for {nominee.name}.\n1. Confirm & Pay\n2. Cancel"
        except (ValueError, Nominee.DoesNotExist):
            response_text = "CON Invalid vote count. Enter number of votes:"

    elif session.current_state == 'CONFIRM_PAYMENT':
        if user_input == '1':
            nominee = Nominee.objects.get(id=session.nominee_id)
            
            # Dispatch STK Payment Prompt Async
            trigger_stk_payment_push(
                phone_number=session.phone_number,
                amount=session.amount_due,
                nominee=nominee,
                votes=session.votes_count
            )
            
            response_text = f"END A MoMo payment prompt has been sent to your phone. Approve to complete your vote. Thank you!"
            session.delete()
        else:
            response_text = "END Voting cancelled. Thank you for choosing Vootely."
            session.delete()

    return HttpResponse(response_text, content_type='text/plain')
```

---

## 5. MoMo STK Push Billing & Webhook Tallies

Once the user approves the payment, the aggregator webhook automatically updates our database:

```python
# payments/views.py
from votes.models import Vote
from nominees.models import Nominee

def trigger_stk_payment_push(phone_number, amount, nominee, votes):
    """
    Triggers Paystack Charge Mobile Money API
    """
    # ... code to call 'https://api.paystack.co/charge' payload containing customer phone, amount ...
    pass

@csrf_exempt
def ussd_payment_webhook(request):
    """
    Webhook target capturing successful transaction receipts.
    """
    payload = json.loads(request.body)
    
    if payload.get('event') == 'charge.success':
        data = payload.get('data')
        phone = data.get('customer', {}).get('phone')
        metadata = data.get('metadata', {})
        
        nominee_id = metadata.get('nominee_id')
        votes_count = metadata.get('votes_count')
        
        # 1. Record Paid Votes
        nominee = Nominee.objects.get(id=nominee_id)
        Vote.objects.create(
            nominee=nominee,
            votes_purchased=votes_count,
            voter_phone=phone,
            payment_status='paid'
        )
        
        # 2. Trigger WebSocket live updates to leaderboard
        # ... broadcast_leaderboard_refresh(nominee.event) ...
        
        # 3. Send SMS confirmation
        # ... send_sms_receipt(phone, nominee, votes_count) ...
        
    return HttpResponse(status=200)
```

---

## 6. Phase-by-Phase Launch Plan

1. **KYC & Aggregator Setup**: Connect with Hubtel/Arkesel to register a shared USSD shortcode and link it to our Django webhook url.
2. **Nominee Model Migration**: Run a database migration adding the unique `short_code` field to the Nominee model, auto-populating existing candidates.
3. **Session & Core Webhook Integration**: Write the `USSDSession` tracking schema and the state-based callback engine.
4. **MoMo STK Integration**: Link the payment dispatcher to Paystack's charge flows.
5. **Testing & QA**: Emulate requests via Postman and mobile test sessions prior to final production rollout.
