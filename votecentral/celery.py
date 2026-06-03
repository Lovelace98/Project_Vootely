import os

from celery import Celery
from celery.schedules import crontab


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'votecentral.settings')

app = Celery('votecentral')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'scan-event-reminders-hourly': {
        'task': 'notifications.scan_event_reminders',
        'schedule': crontab(minute=0),
    },
    'retry-failed-notifications-every-15-minutes': {
        'task': 'notifications.retry_failed_notifications',
        'schedule': crontab(minute='*/15'),
    },
    'scan-voter-turnout-reminders-hourly': {
        'task': 'notifications.scan_voter_turnout_reminders',
        'schedule': crontab(minute=0),
    },
}
