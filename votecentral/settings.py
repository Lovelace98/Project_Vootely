import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
import environ

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

SECRET_KEY = env('SECRET_KEY', default='votecentral-dev-secret-key')
DEBUG = env.bool('DEBUG', default=False)
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

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
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
    'wallets',
    'notifications',
]

SITE_ID = 1

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'votecentral-phase-1',
    },
}

ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_FORMS = {
    'signup': 'accounts.forms.CustomSignupForm',
}
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default='votecentral.email_backend.ReadableConsoleEmailBackend',
)
EMAIL_HOST = env('EMAIL_HOST', default='localhost')
EMAIL_PORT = env.int('EMAIL_PORT', default=25)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=False)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='VoteCentral <no-reply@localhost>')
SERVER_EMAIL = env('SERVER_EMAIL', default=DEFAULT_FROM_EMAIL)
NOTIFICATION_ADMIN_EMAILS = env.list('NOTIFICATION_ADMIN_EMAILS', default=[])
NOTIFICATION_REMINDER_LEAD_HOURS = env.int('NOTIFICATION_REMINDER_LEAD_HOURS', default=24)
NOTIFICATION_RETRY_LIMIT = env.int('NOTIFICATION_RETRY_LIMIT', default=3)
SMS_PROVIDER = env('SMS_PROVIDER', default='').strip().lower()
HUBTEL_SMS_BASE_URL = env(
    'HUBTEL_SMS_BASE_URL',
    default='https://smsc.hubtel.com/v1/messages',
)
HUBTEL_CLIENT_ID = env('HUBTEL_CLIENT_ID', default='')
HUBTEL_CLIENT_SECRET = env('HUBTEL_CLIENT_SECRET', default='')
HUBTEL_SMS_FROM = env('HUBTEL_SMS_FROM', default='')
HUBTEL_TIMEOUT_SECONDS = env.int('HUBTEL_TIMEOUT_SECONDS', default=15)

TAILWIND_CLI_PATH = BASE_DIR / 'bin' / 'tailwindcss'
TAILWIND_CLI_SRC_CSS = 'css/src.css'
TAILWIND_CLI_DIST_CSS = 'css/tailwind.css'

PAYSTACK_SECRET_KEY = env('PAYSTACK_SECRET_KEY', default='')
PAYSTACK_PUBLIC_KEY = env('PAYSTACK_PUBLIC_KEY', default='')
PAYSTACK_WEBHOOK_SECRET = env(
    'PAYSTACK_WEBHOOK_SECRET',
    default=PAYSTACK_SECRET_KEY,
)
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
