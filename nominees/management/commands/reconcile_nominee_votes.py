from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import DecimalField, IntegerField, Sum, Value
from django.db.models.functions import Coalesce

from nominees.models import Nominee
from votes.models import VotePurchase


class Command(BaseCommand):
    help = 'Reconciles cached total_votes and total_amount on Nominees against VotePurchase records.'

    def handle(self, *args, **options):
        self.stdout.write('Starting nominee vote reconciliation...')
        updated = 0

        for nominee in Nominee.objects.all():
            totals = VotePurchase.objects.filter(nominee=nominee).aggregate(
                votes=Coalesce(Sum('quantity'), Value(0), output_field=IntegerField()),
                amount=Coalesce(
                    Sum('amount_paid'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            reconciled_votes = totals['votes']
            reconciled_amount = totals['amount']

            if (
                nominee.total_votes != reconciled_votes
                or nominee.total_amount != reconciled_amount
            ):
                nominee.total_votes = reconciled_votes
                nominee.total_amount = reconciled_amount
                nominee.save(update_fields=['total_votes', 'total_amount'])
                updated += 1
                self.stdout.write(
                    f'Nominee #{nominee.pk} ({nominee.name}): updated to '
                    f'votes={reconciled_votes}, amount={reconciled_amount}'
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Reconciliation complete. Total updated: {updated}'
            )
        )

