from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import Notification
from .phone import format_hubtel_phone_number


@dataclass
class NotificationSendResult:
    deliveries: int = 1
    provider: str = ''
    provider_status: str = ''
    provider_message_id: str = ''
    provider_payload: dict = field(default_factory=dict)
    provider_error_code: str = ''


class NotificationDeliveryError(RuntimeError):
    def __init__(
        self,
        message,
        *,
        provider='',
        provider_status='',
        provider_payload=None,
        provider_error_code='',
    ):
        super().__init__(message)
        self.provider = provider
        self.provider_status = provider_status
        self.provider_payload = provider_payload or {}
        self.provider_error_code = provider_error_code


class EmailAdapter:
    provider_name = 'django-email'

    def send(self, notification):
        email = EmailMultiAlternatives(
            subject=notification.subject,
            body=notification.body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[notification.recipient_email],
        )
        if notification.body_html:
            email.attach_alternative(notification.body_html, 'text/html')
        deliveries = email.send()
        return NotificationSendResult(
            deliveries=deliveries,
            provider=self.provider_name,
            provider_status='accepted' if deliveries > 0 else 'rejected',
        )


class HubtelSmsAdapter:
    provider_name = 'hubtel'

    def send(self, notification):
        if not settings.HUBTEL_CLIENT_ID or not settings.HUBTEL_CLIENT_SECRET:
            raise NotificationDeliveryError(
                'Hubtel SMS credentials are not configured.',
                provider=self.provider_name,
                provider_status='configuration_error',
            )
        if not settings.HUBTEL_SMS_FROM:
            raise NotificationDeliveryError(
                'HUBTEL_SMS_FROM is not configured.',
                provider=self.provider_name,
                provider_status='configuration_error',
            )

        recipient_phone = format_hubtel_phone_number(notification.recipient_phone)
        if not recipient_phone:
            raise NotificationDeliveryError(
                'A valid SMS recipient phone number is required.',
                provider=self.provider_name,
                provider_status='invalid_recipient',
            )

        endpoint = settings.HUBTEL_SMS_BASE_URL.rstrip('/')
        if not endpoint.endswith('/send'):
            endpoint = f'{endpoint}/send'

        payload = {
            'From': settings.HUBTEL_SMS_FROM,
            'To': recipient_phone,
            'Content': notification.body_text,
        }
        response = requests.post(
            endpoint,
            json=payload,
            auth=(settings.HUBTEL_CLIENT_ID, settings.HUBTEL_CLIENT_SECRET),
            timeout=settings.HUBTEL_TIMEOUT_SECONDS,
        )
        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {'raw': response.text}

        if response.status_code < 200 or response.status_code >= 300:
            raise NotificationDeliveryError(
                response_payload.get('message')
                or response.reason
                or 'Hubtel SMS request failed.',
                provider=self.provider_name,
                provider_status=str(response_payload.get('responseCode') or response.status_code),
                provider_payload=response_payload,
                provider_error_code=str(response_payload.get('responseCode') or response.status_code),
            )

        data = response_payload.get('data') or {}
        return NotificationSendResult(
            deliveries=1,
            provider=self.provider_name,
            provider_status=str(data.get('status') or response_payload.get('responseCode') or 'accepted'),
            provider_message_id=str(
                data.get('messageId')
                or data.get('messageID')
                or response_payload.get('messageId')
                or ''
            ),
            provider_payload=response_payload,
            provider_error_code=str(response_payload.get('responseCode') or ''),
        )


def get_notification_adapter(notification):
    if notification.channel == Notification.Channel.SMS:
        return HubtelSmsAdapter()
    return EmailAdapter()
