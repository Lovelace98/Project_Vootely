from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def public_app_url_check(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    if (getattr(settings, 'PUBLIC_APP_URL', '') or '').strip():
        return []
    return [
        Error(
            'PUBLIC_APP_URL must be configured when DEBUG is False.',
            hint='Set PUBLIC_APP_URL to your deployed https:// domain so emails, SMS, exports, and share links never use localhost or temporary hosts.',
            id='votecentral.E001',
        )
    ]
