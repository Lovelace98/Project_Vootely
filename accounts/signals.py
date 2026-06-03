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


from allauth.account.signals import email_confirmed

@receiver(email_confirmed)
def send_welcome_email(sender, request, email_address, **kwargs):
    user = email_address.user
    from notifications.services import queue_notification, create_in_app_notification
    from notifications.models import Notification
    
    # Queue welcome email
    queue_notification(
        channel=Notification.Channel.EMAIL,
        event_type=Notification.EventType.ORGANIZER_WELCOME,
        recipient_email=user.email,
        recipient_name=user.get_full_name() or user.email,
        dedupe_parts=(user.pk, 'welcome'),
    )
    
    # Create welcome in-app notification / audit log
    create_in_app_notification(
        user=user,
        title="Welcome to Vootely!",
        message="Thank you for confirming your email. You can now create paid competitions and secure elections in your organizer dashboard!",
        link="/dashboard/",
        level="success",
    )

