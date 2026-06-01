from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event
from nominees.models import Nominee
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.services import post_payment_ledger_transaction


class Command(BaseCommand):
    help = 'Seed a local Phase 1 demo organizer, event, nominees, and sample purchases.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            default='demo@votecentral.local',
            help='Organizer email for the demo account.',
        )
        parser.add_argument(
            '--password',
            default='demo-pass-123',
            help='Organizer password for the demo account.',
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        email = options['email']
        password = options['password']

        organizer, created = user_model.objects.get_or_create(
            email=email,
            defaults={'is_active': True},
        )
        if created:
            organizer.set_password(password)
            organizer.save(update_fields=['password'])
            self.stdout.write(self.style.SUCCESS(f'Created organizer {email}'))
        else:
            organizer.set_password(password)
            organizer.save(update_fields=['password'])
            self.stdout.write(self.style.WARNING(f'Updated password for existing organizer {email}'))

        now = timezone.now()
        event, _ = Event.objects.get_or_create(
            owner=organizer,
            slug='demo-campus-star',
            defaults={
                'title': 'Demo Campus Star',
                'description': 'Demo paid competition for local VoteCentral testing.',
                'currency': 'GHS',
                'vote_price': Decimal('2.00'),
                'start_at': now - timedelta(hours=2),
                'end_at': now + timedelta(days=7),
                'status': Event.Status.PUBLISHED,
                'is_public': True,
                'published_at': now - timedelta(hours=1),
            },
        )
        if event.status != Event.Status.PUBLISHED:
            event.status = Event.Status.PUBLISHED
            event.published_at = event.published_at or now
            event.start_at = event.start_at or now - timedelta(hours=2)
            event.end_at = event.end_at or now + timedelta(days=7)
            event.vote_price = event.vote_price or Decimal('2.00')
            event.save()

        nominees = []
        for index, name in enumerate(['Ama Serwaa', 'Kojo Mensah', 'Esi Arthur'], start=1):
            nominee, _ = Nominee.objects.get_or_create(
                event=event,
                slug=name.lower().replace(' ', '-'),
                defaults={
                    'name': name,
                    'bio': f'{name} is part of the local VoteCentral demo event.',
                    'display_order': index,
                    'is_active': True,
                },
            )
            nominees.append(nominee)

        sample_rows = [
            ('demo-pay-001', nominees[0], 12, Decimal('24.00'), 'Ama Supporter', 'ama@example.com'),
            ('demo-pay-002', nominees[1], 8, Decimal('16.00'), 'Kojo Supporter', 'kojo@example.com'),
            ('demo-pay-003', nominees[2], 5, Decimal('10.00'), 'Esi Supporter', 'esi@example.com'),
        ]

        for reference, nominee, quantity, amount, voter_name, voter_email in sample_rows:
            attempt, _ = PaymentAttempt.objects.get_or_create(
                gateway_reference=reference,
                defaults={
                    'event': event,
                    'nominee': nominee,
                    'amount': amount,
                    'currency': 'GHS',
                    'vote_quantity': quantity,
                    'voter_name': voter_name,
                    'voter_email': voter_email,
                    'status': PaymentAttempt.Status.PAID,
                    'completed_at': now - timedelta(minutes=quantity),
                },
            )
            if not hasattr(attempt, 'vote_purchase'):
                VotePurchase.objects.create(
                    event=event,
                    nominee=nominee,
                    payment_attempt=attempt,
                    quantity=quantity,
                    amount_paid=amount,
                    currency='GHS',
                    payment_reference=reference,
                    voter_name=voter_name,
                    voter_email=voter_email,
                    paid_at=attempt.completed_at or now,
                    metadata={'seeded': True},
                )
            post_payment_ledger_transaction(attempt)

        self.stdout.write(self.style.SUCCESS('Seeded demo organizer, event, nominees, and purchases.'))
        self.stdout.write(self.style.SUCCESS(f'Login with: {email} / {password}'))
        self.stdout.write(self.style.SUCCESS(f'Public event URL: /events/{event.slug}/'))
