import os
from celery import Celery

# Set default Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'botcentral.settings')

app = Celery('botcentral')

# Load config from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

from celery.schedules import crontab

app.conf.beat_schedule = {
    'sync-all-sheets-hourly': {
        'task': 'core.tasks.sync_all_projects',
        'schedule': crontab(minute=0),  # Every hour at :00
    },
}
