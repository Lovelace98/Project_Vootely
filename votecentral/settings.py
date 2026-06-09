import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
import environ
from django.core.management.utils import get_random_secret_key
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

# Some shells/tools export DEBUG=release for their own lifecycle. Treat only
# explicit boolean DEBUG values as Django settings so local .env can still work.
raw_debug = os.environ.get('DEBUG')
if raw_debug and raw_debug.lower() not in {
    '1',
    '0',
    'true',
    'false',
    'yes',
    'no',
    'on',
    'off',
}:
    os.environ.pop('DEBUG')
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

DEBUG = env.bool('DEBUG', default=False)
SECRET_KEY = env(
    'SECRET_KEY',
    default=(
        os.environ.get('DJANGO_RUNTIME_SECRET_KEY')
        or get_random_secret_key()
    ),
)
TIME_ZONE = env('TIME_ZONE', default='Africa/Accra')
USE_I18N = True
USE_TZ = True
LANGUAGE_CODE = 'en-us'
PUBLIC_APP_URL = env('PUBLIC_APP_URL', default='').strip().rstrip('/')

default_allowed_hosts = ['*'] if DEBUG else ['localhost', '127.0.0.1']
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=default_allowed_hosts)
if PUBLIC_APP_URL:
    public_host = urlparse(PUBLIC_APP_URL).hostname
    if public_host and public_host not in ALLOWED_HOSTS and '*' not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(public_host)

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])
if PUBLIC_APP_URL and PUBLIC_APP_URL not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(PUBLIC_APP_URL)

if DEBUG:
    for local_origin in ['http://127.0.0.1:8000', 'http://localhost:8000']:
        if local_origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(local_origin)

SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=not DEBUG)
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=not DEBUG)
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    default=not DEBUG,
)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=not DEBUG)
USE_X_FORWARDED_PROTO = env.bool('USE_X_FORWARDED_PROTO', default=not DEBUG)
if USE_X_FORWARDED_PROTO:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = env('SECURE_REFERRER_POLICY', default='same-origin')
X_FRAME_OPTIONS = env('X_FRAME_OPTIONS', default='DENY')
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

INSTALLED_APPS = [
    'daphne',
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.sitemaps',
    'django_htmx',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'channels',
    'django_celery_results',
    'django_tailwind_cli',
    'accounts',
    'events',
    'elections',
    'nominees',
    'votes',
    'payments',
    'ticketing',
    'wallets',
    'notifications',
]

UNFOLD = {
    'SITE_TITLE': 'Vootely Admin',
    'SITE_HEADER': 'Vootely',
    'SITE_SUBHEADER': 'Platform operations',
    'SITE_URL': '/',
    'SITE_LOGO': {
        'light': lambda request: static('images/vootely_logo_light.png'),
        'dark': lambda request: static('images/vootely_logo_dark.png'),
    },
    'SITE_FAVICONS': [
        {
            'rel': 'icon',
            'sizes': '32x32',
            'type': 'image/png',
            'href': lambda request: static('images/favicon.png'),
        },
    ],
    'SITE_SYMBOL': 'how_to_vote',
    'BORDER_RADIUS': '6px',
    'DASHBOARD_CALLBACK': 'votecentral.admin_dashboard.dashboard_callback',
    'ENVIRONMENT': 'votecentral.admin_dashboard.environment_callback',
    'SHOW_BACK_BUTTON': True,
    'SHOW_VIEW_ON_SITE': True,
    'COMMAND': {
        'search_models': True,
    },
    'SIDEBAR': {
        'show_search': True,
        'command_search': True,
        'show_all_applications': False,
        'navigation': [
            {
                'title': _('Overview'),
                'separator': True,
                'items': [
                    {
                        'title': _('Dashboard'),
                        'icon': 'dashboard',
                        'link': reverse_lazy('admin:index'),
                    },
                ],
            },
            {
                'title': _('Platform'),
                'separator': True,
                'collapsible': True,
                'items': [
                    {'title': _('Users'), 'icon': 'group', 'link': reverse_lazy('admin:accounts_user_changelist')},
                    {'title': _('Events'), 'icon': 'event', 'link': reverse_lazy('admin:events_event_changelist')},
                    {'title': _('Contact Inquiries'), 'icon': 'contact_mail', 'link': reverse_lazy('admin:events_contactinquiry_changelist')},
                ],
            },
            {
                'title': _('Paid Voting'),
                'separator': True,
                'collapsible': True,
                'items': [
                    {'title': _('Nominees'), 'icon': 'workspace_premium', 'link': reverse_lazy('admin:nominees_nominee_changelist')},
                    {'title': _('Payment Attempts'), 'icon': 'payments', 'link': reverse_lazy('admin:payments_paymentattempt_changelist')},
                    {'title': _('Vote Purchases'), 'icon': 'how_to_vote', 'link': reverse_lazy('admin:votes_votepurchase_changelist')},
                ],
            },
            {
                'title': _('Secure Elections'),
                'separator': True,
                'collapsible': True,
                'items': [
                    {'title': _('Configs'), 'icon': 'tune', 'link': reverse_lazy('admin:elections_electionconfig_changelist')},
                    {'title': _('Positions'), 'icon': 'ballot', 'link': reverse_lazy('admin:elections_electionposition_changelist')},
                    {'title': _('Candidates'), 'icon': 'badge', 'link': reverse_lazy('admin:elections_electioncandidate_changelist')},
                    {'title': _('Voters'), 'icon': 'person_check', 'link': reverse_lazy('admin:elections_electionvoter_changelist')},
                    {'title': _('Credentials'), 'icon': 'vpn_key', 'link': reverse_lazy('admin:elections_electioncredential_changelist')},
                    {'title': _('Ballots'), 'icon': 'fact_check', 'link': reverse_lazy('admin:elections_ballot_changelist')},
                    {'title': _('Tallies'), 'icon': 'leaderboard', 'link': reverse_lazy('admin:elections_electiontallysnapshot_changelist')},
                    {'title': _('Audit Logs'), 'icon': 'history', 'link': reverse_lazy('admin:elections_electionauditlog_changelist')},
                    {'title': _('Invoices'), 'icon': 'receipt_long', 'link': reverse_lazy('admin:elections_electioninvoice_changelist')},
                ],
            },
            {
                'title': _('Finance'),
                'separator': True,
                'collapsible': True,
                'items': [
                    {'title': _('Wallet Accounts'), 'icon': 'account_balance_wallet', 'link': reverse_lazy('admin:wallets_walletaccount_changelist')},
                    {'title': _('Ledger Transactions'), 'icon': 'account_balance', 'link': reverse_lazy('admin:wallets_ledgertransaction_changelist')},
                    {'title': _('Ledger Entries'), 'icon': 'list_alt', 'link': reverse_lazy('admin:wallets_ledgerentry_changelist')},
                    {'title': _('Withdrawals'), 'icon': 'request_quote', 'link': reverse_lazy('admin:wallets_withdrawalrequest_changelist')},
                ],
            },
            {
                'title': _('Messaging'),
                'separator': True,
                'collapsible': True,
                'items': [
                    {'title': _('Notifications'), 'icon': 'mark_email_unread', 'link': reverse_lazy('admin:notifications_notification_changelist')},
                    {'title': _('In-App Notifications'), 'icon': 'notifications', 'link': reverse_lazy('admin:notifications_inappnotification_changelist')},
                ],
            },
        ],
    },
}

SITE_ID = 1

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'votecentral.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'notifications.context_processors.unread_notifications',
                'payments.context_processors.paystack_settings',
                'votecentral.context_processors.canonical_url',
                'votecentral.context_processors.support_contact',
                'votecentral.context_processors.dashboard_greeting',
            ],
        },
    },
]

WSGI_APPLICATION = 'votecentral.wsgi.application'
ASGI_APPLICATION = 'votecentral.asgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=env(
            'DATABASE_URL',
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        ),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default']['OPTIONS'] = {
        'timeout': 20,  # Wait up to 20 seconds for the lock to clear
        'init_command': (
            'PRAGMA journal_mode=WAL;'
            'PRAGMA synchronous=NORMAL;'
            'PRAGMA busy_timeout=20000;'
        ),
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
    {
        'NAME': 'accounts.validators.ComplexityValidator',
    },
]

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
    BASE_DIR / 'assets',
]

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Supabase / S3 Media Storage Settings
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default=None)
if AWS_ACCESS_KEY_ID:
    AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_ENDPOINT_URL = env('AWS_S3_ENDPOINT_URL')
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_VERIFY = True

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage" if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage" if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

REDIS_URL = env('REDIS_URL', default='')
if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [REDIS_URL]},
        },
    }
    CELERY_BROKER_URL = REDIS_URL
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }
    CELERY_BROKER_URL = 'memory://'

CELERY_RESULT_BACKEND = 'django-db'
CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=True)
CELERY_TASK_EAGER_PROPAGATES = env.bool('CELERY_TASK_EAGER_PROPAGATES', default=True)

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'TIMEOUT': 300,
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'votecentral-phase-1',
        },
    }

if not DEBUG:
    TEMPLATES[0]['APP_DIRS'] = False
    TEMPLATES[0]['OPTIONS']['loaders'] = [
        (
            'django.template.loaders.cached.Loader',
            [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
        )
    ]

ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_RATE_LIMITS = {
    'login_failed': '5/5m',
    'signup': '3/10m',
    'confirm_email': '5/5m',
}
ACCOUNT_FORMS = {
    'signup': 'accounts.forms.CustomSignupForm',
}
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = ''
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

SUPPORT_EMAIL = env('SUPPORT_EMAIL', default='support@vootely.com')
SUPPORT_PHONE = env('SUPPORT_PHONE', default='+233 54 898 8503')
SUPPORT_NAME = env('SUPPORT_NAME', default='Vootely')
SECURITY_EMAIL = env('SECURITY_EMAIL', default='security@vootely.com')
BREVO_SENDER_EMAIL = env('BREVO_SENDER_EMAIL', default='853e5e001@smtp-brevo.com')
USSD_VOTER_EMAIL = env('USSD_VOTER_EMAIL', default='ussd-voter@vootely.com')

EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default=(
        'votecentral.email_backend.ReadableConsoleEmailBackend'
        if DEBUG
        else 'django.core.mail.backends.smtp.EmailBackend'
    ),
)
EMAIL_HOST = env('EMAIL_HOST', default='localhost')
EMAIL_PORT = env.int('EMAIL_PORT', default=25)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=False)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='Vootely <no-reply@localhost>')
SERVER_EMAIL = env('SERVER_EMAIL', default=DEFAULT_FROM_EMAIL)
NOTIFICATION_ADMIN_EMAILS = env.list('NOTIFICATION_ADMIN_EMAILS', default=['support@vootely.com'])
NOTIFICATION_REMINDER_LEAD_HOURS = env.int('NOTIFICATION_REMINDER_LEAD_HOURS', default=24)
NOTIFICATION_RETRY_LIMIT = env.int('NOTIFICATION_RETRY_LIMIT', default=3)
SMS_PROVIDER = env('SMS_PROVIDER', default='arkesel').strip().lower()
HUBTEL_SMS_BASE_URL = env(
    'HUBTEL_SMS_BASE_URL',
    default='https://smsc.hubtel.com/v1/messages',
)
HUBTEL_CLIENT_ID = env('HUBTEL_CLIENT_ID', default='')
HUBTEL_CLIENT_SECRET = env('HUBTEL_CLIENT_SECRET', default='')
HUBTEL_SMS_FROM = env('HUBTEL_SMS_FROM', default='')
HUBTEL_TIMEOUT_SECONDS = env.int('HUBTEL_TIMEOUT_SECONDS', default=15)

ARKESEL_API_KEY = env('ARKESEL_API_KEY', default='')
ARKESEL_SMS_FROM = env('ARKESEL_SMS_FROM', default='Vootely')
ARKESEL_SMS_BASE_URL = env('ARKESEL_SMS_BASE_URL', default='https://sms.arkesel.com/api/v2/sms/send')
USSD_SHORT_CODE = env('USSD_SHORT_CODE', default='*920*24#')

EMAIL_PROVIDER = env('EMAIL_PROVIDER', default='').strip().lower()
BREVO_API_KEY = env('BREVO_API_KEY', default='')
BREVO_API_URL = env('BREVO_API_URL', default='https://api.brevo.com/v3/smtp/email')


TAILWIND_CLI_PATH = BASE_DIR / 'bin' / 'tailwindcss'
TAILWIND_CLI_SRC_CSS = 'css/src.css'
TAILWIND_CLI_DIST_CSS = 'css/tailwind.css'

PAYSTACK_SECRET_KEY = env('PAYSTACK_SECRET_KEY', default='')
PAYSTACK_PUBLIC_KEY = env('PAYSTACK_PUBLIC_KEY', default='')
PAYSTACK_WEBHOOK_SECRET = env('PAYSTACK_WEBHOOK_SECRET', default='')
PAYSTACK_INITIALIZE_URL = env(
    'PAYSTACK_INITIALIZE_URL',
    default='https://api.paystack.co/transaction/initialize',
)
PAYSTACK_CALLBACK_URL = env(
    'PAYSTACK_CALLBACK_URL',
    default=(
        f'{PUBLIC_APP_URL}/payments/paystack/callback/'
        if PUBLIC_APP_URL
        else 'http://localhost:8000/payments/paystack/callback/'
    ),
)
PAYSTACK_WEBHOOK_URL = env(
    'PAYSTACK_WEBHOOK_URL',
    default=(
        f'{PUBLIC_APP_URL}/payments/paystack/webhook/'
        if PUBLIC_APP_URL
        else 'http://localhost:8000/payments/paystack/webhook/'
    ),
)
PLATFORM_COMMISSION_RATE = Decimal(
    env('PLATFORM_COMMISSION_RATE', default='0.10')
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': env('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
    },
}
