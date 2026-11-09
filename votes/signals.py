from decimal import Decimal

from django.db.models import Case, F, Value, When
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import VotePurchase


@receiver(post_save, sender=VotePurchase, dispatch_uid='vote_purchase_counter_increment')
def increment_nominee_vote_counters(sender, instance, created, **kwargs):
    if created and instance.nominee_id:
        from nominees.models import Nominee

        Nominee.objects.filter(pk=instance.nominee_id).update(
            total_votes=F('total_votes') + instance.quantity,
            total_amount=F('total_amount') + instance.amount_paid,
        )


@receiver(post_delete, sender=VotePurchase, dispatch_uid='vote_purchase_counter_decrement')
def decrement_nominee_vote_counters(sender, instance, **kwargs):
    if instance.nominee_id:
        from nominees.models import Nominee

        Nominee.objects.filter(pk=instance.nominee_id).update(
            total_votes=Case(
                When(total_votes__gt=instance.quantity, then=F('total_votes') - instance.quantity),
                default=Value(0),
            ),
            total_amount=Case(
                When(total_amount__gt=instance.amount_paid, then=F('total_amount') - instance.amount_paid),
                default=Value(Decimal('0.00')),
            ),
        )

