from django.apps import apps
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


@receiver(post_save, sender=User)
def ensure_user_wallet(sender, instance, created, **kwargs):
    if not created:
        return

    wallet_model = apps.get_model('wallets', 'WalletAccount')
    wallet_model.objects.get_or_create(
        owner=instance,
        defaults={
            'kind': wallet_model.Kind.ORGANIZER,
            'code': f'organizer-{instance.pk}',
            'name': instance.email,
        },
    )
