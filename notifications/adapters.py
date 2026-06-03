from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import Notification


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


def resolve_sms_provider():
    provider = getattr(settings, 'SMS_PROVIDER', '').strip().lower()
    if provider == 'arkesel':
        return provider
    if settings.ARKESEL_API_KEY and settings.ARKESEL_SMS_FROM:
        return 'arkesel'
    return ''


def resolve_email_provider():
    provider = getattr(settings, 'EMAIL_PROVIDER', '').strip().lower()
    if provider == 'brevo':
        return 'brevo'
    if settings.BREVO_API_KEY:
        return 'brevo'
    return 'django-email'


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


class ArkeselSmsAdapter:
    provider_name = 'arkesel'

    @staticmethod
    def _normalize_response_payload(payload):
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {
                'status': 'success',
                'data': payload,
            }
        return {
            'status': 'error',
            'message': 'Unexpected Arkesel response format.',
            'raw': payload,
        }

    @staticmethod
    def _first_message_id(data):
        if isinstance(data, list) and data:
            first_item = data[0] or {}
            if isinstance(first_item, dict):
                return str(
                    first_item.get('id')
                    or first_item.get('ID')
                    or first_item.get('message_id')
                    or first_item.get('messageId')
                    or ''
                )
            return ''
        if isinstance(data, dict):
            return str(
                data.get('id')
                or data.get('ID')
                or data.get('message_id')
                or data.get('messageId')
                or ''
            )
        return ''

    def _send_v2(self, notification):
        endpoint = getattr(settings, 'ARKESEL_SMS_BASE_URL', 'https://sms.arkesel.com/api/v2/sms/send').rstrip('/')
        if not endpoint.endswith('/sms/send') and not endpoint.endswith('/send'):
            endpoint = f'{endpoint}/sms/send'

        payload = {
            'sender': getattr(settings, 'ARKESEL_SMS_FROM', 'Vootely'),
            'recipients': [notification.recipient_phone],
            'message': notification.body_text,
        }
        headers = {
            'api-key': settings.ARKESEL_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=15,
        )
        try:
            response_payload = response.json()
        except Exception:
            response_payload = {'raw': response.text}
        response_payload = self._normalize_response_payload(response_payload)

        status = (response_payload.get('status') or '').strip().lower()
        code = str(response_payload.get('code') or response.status_code)
        message = (response_payload.get('message') or '').strip()

        if response.status_code not in (200, 201) or status != 'success':
            raise NotificationDeliveryError(
                message or f'Arkesel V2 SMS failed with code {code}',
                provider=self.provider_name,
                provider_status=status or str(response.status_code),
                provider_payload=response_payload,
                provider_error_code=code,
            )

        data = response_payload.get('data')
        return NotificationSendResult(
            deliveries=1,
            provider=self.provider_name,
            provider_status='success',
            provider_message_id=self._first_message_id(data or response_payload),
            provider_payload=response_payload,
            provider_error_code=code,
        )

    def _send_v1(self, notification):
        endpoint = getattr(settings, 'ARKESEL_SMS_LEGACY_URL', 'https://sms.arkesel.com/sms/api')
        sender = getattr(settings, 'ARKESEL_SMS_FROM', 'Vootely')
        recipient_phone = notification.recipient_phone.lstrip('+')

        payload = {
            'action': 'send-sms',
            'api_key': settings.ARKESEL_API_KEY,
            'to': recipient_phone,
            'from': sender,
            'sms': notification.body_text,
        }
        response = requests.post(
            endpoint,
            params={'api_key': settings.ARKESEL_API_KEY},
            json=payload,
            timeout=15,
        )
        try:
            response_payload = response.json()
        except Exception:
            response_payload = {'raw': response.text}

        response_code = None
        response_message = ''
        if isinstance(response_payload, dict):
            response_code = response_payload.get('code')
            response_message = (response_payload.get('message') or '').strip()
        elif isinstance(response_payload, list) and response_payload:
            response_code = response.status_code
            first_item = response_payload[0] or {}
            if isinstance(first_item, dict):
                response_message = (first_item.get('message') or '').strip()

        code = str(response_code or response.status_code)
        if response.status_code != 200 or str(response_code or '').upper() != 'OK':
            raise NotificationDeliveryError(
                response_message or f'Arkesel V1 SMS failed with code {code}',
                provider=self.provider_name,
                provider_status=str(response_code or response.status_code),
                provider_payload=response_payload,
                provider_error_code=code,
            )

        return NotificationSendResult(
            deliveries=1,
            provider=self.provider_name,
            provider_status='ok',
            provider_message_id=self._first_message_id(response_payload),
            provider_payload=response_payload,
            provider_error_code=code,
        )

    def send(self, notification):
        if not settings.ARKESEL_API_KEY:
            raise NotificationDeliveryError(
                'Arkesel SMS credentials are not configured.',
                provider=self.provider_name,
                provider_status='configuration_error',
            )
        recipient_phone = notification.recipient_phone
        if not recipient_phone:
            raise NotificationDeliveryError(
                'A valid SMS recipient phone number is required.',
                provider=self.provider_name,
                provider_status='invalid_recipient',
            )

        try:
            return self._send_v2(notification)
        except NotificationDeliveryError as exc:
            auth_failure = (
                str(exc.provider_error_code) == '401'
                or 'invalid key' in str(exc).lower()
                or 'authentication' in str(exc).lower()
            )
            if not auth_failure:
                raise
            return self._send_v1(notification)


class BrevoEmailAdapter:
    provider_name = 'brevo'

    def send(self, notification):
        if not settings.BREVO_API_KEY:
            raise NotificationDeliveryError(
                'Brevo API key is not configured.',
                provider=self.provider_name,
                provider_status='configuration_error',
            )

        endpoint = getattr(settings, 'BREVO_API_URL', 'https://api.brevo.com/v3/smtp/email')
        headers = {
            'api-key': settings.BREVO_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        # Parse sender details from DEFAULT_FROM_EMAIL
        sender_name, sender_email = 'Vootely', '853e5e001@smtp-brevo.com'
        from_email_config = getattr(settings, 'DEFAULT_FROM_EMAIL', '')
        if '<' in from_email_config and '>' in from_email_config:
            sender_name = from_email_config.split('<')[0].strip()
            sender_email = from_email_config.split('<')[1].split('>')[0].strip()
        elif from_email_config:
            sender_email = from_email_config.strip()
            sender_name = 'Vootely'

        payload = {
            'sender': {'name': sender_name, 'email': sender_email},
            'to': [{
                'email': notification.recipient_email,
                'name': notification.recipient_name or notification.recipient_email
            }],
            'subject': notification.subject,
            'textContent': notification.body_text,
        }

        if notification.body_html:
            payload['htmlContent'] = notification.body_html

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=15,
            )
            response_payload = response.json()
        except Exception as exc:
            raise NotificationDeliveryError(
                f'Brevo HTTP request failed: {exc}',
                provider=self.provider_name,
                provider_status='http_error',
            )

        if response.status_code not in (200, 201, 202) or 'messageId' not in response_payload:
            raise NotificationDeliveryError(
                response_payload.get('message') or f'Brevo API returned error status {response.status_code}',
                provider=self.provider_name,
                provider_status='failed',
                provider_payload=response_payload,
                provider_error_code=response_payload.get('code') or str(response.status_code),
            )

        return NotificationSendResult(
            deliveries=1,
            provider=self.provider_name,
            provider_status='sent',
            provider_message_id=response_payload.get('messageId', ''),
            provider_payload=response_payload,
        )


def get_notification_adapter(notification):
    provider = (getattr(notification, 'provider', '') or '').strip().lower()
    if notification.channel == Notification.Channel.SMS:
        provider = provider or resolve_sms_provider()
        if provider == 'arkesel':
            return ArkeselSmsAdapter()
        raise NotificationDeliveryError(
            'No SMS provider is configured.',
            provider='',
            provider_status='configuration_error',
        )
    else:
        if provider == 'brevo':
            return BrevoEmailAdapter()
        if provider in {'django-email', 'console'}:
            return EmailAdapter()
        provider = resolve_email_provider()
        if provider == 'brevo':
            return BrevoEmailAdapter()
        return EmailAdapter()
