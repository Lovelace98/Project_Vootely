from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from elections.models import Ballot, BallotSelection
from events.models import Event
from events.performance import bump_event_cache, bump_notification_cache, bump_organizer_cache
from nominees.models import CompetitionCategory, NominationSubmission, Nominee
from notifications.models import InAppNotification
from payments.models import PaymentAttempt
from votes.models import VotePurchase
from wallets.models import LedgerEntry, WithdrawalRequest


def _bump_event_and_owner(event):
    if not event:
        return
    bump_event_cache(event.pk)
    bump_organizer_cache(event.owner_id)


@receiver([post_save, post_delete], sender=Event, dispatch_uid='event_perf_cache_invalidation')
def invalidate_event_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance)


@receiver([post_save, post_delete], sender=Nominee, dispatch_uid='nominee_perf_cache_invalidation')
def invalidate_nominee_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=CompetitionCategory, dispatch_uid='competition_category_perf_cache_invalidation')
def invalidate_category_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=NominationSubmission, dispatch_uid='nomination_submission_perf_cache_invalidation')
def invalidate_nomination_submission_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=PaymentAttempt, dispatch_uid='payment_attempt_perf_cache_invalidation')
def invalidate_payment_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=VotePurchase, dispatch_uid='vote_purchase_perf_cache_invalidation')
def invalidate_vote_purchase_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=LedgerEntry, dispatch_uid='ledger_entry_perf_cache_invalidation')
def invalidate_ledger_cache(sender, instance, **kwargs):
    owner_id = getattr(instance.account, 'owner_id', None)
    if owner_id:
        bump_organizer_cache(owner_id)
    payment_attempt = getattr(instance.transaction, 'payment_attempt', None)
    if payment_attempt:
        _bump_event_and_owner(payment_attempt.event)


@receiver([post_save, post_delete], sender=WithdrawalRequest, dispatch_uid='withdrawal_perf_cache_invalidation')
def invalidate_withdrawal_cache(sender, instance, **kwargs):
    bump_organizer_cache(instance.organizer_id)


@receiver([post_save, post_delete], sender=Ballot, dispatch_uid='ballot_perf_cache_invalidation')
def invalidate_ballot_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.event)


@receiver([post_save, post_delete], sender=BallotSelection, dispatch_uid='ballot_selection_perf_cache_invalidation')
def invalidate_ballot_selection_cache(sender, instance, **kwargs):
    _bump_event_and_owner(instance.position.event)


@receiver([post_save, post_delete], sender=InAppNotification, dispatch_uid='notification_perf_cache_invalidation')
def invalidate_notification_cache(sender, instance, **kwargs):
    bump_notification_cache(instance.user_id)
