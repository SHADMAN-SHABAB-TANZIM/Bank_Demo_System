import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("banksys")

# Read CELERY_* settings from Django settings.py (see the
# CELERY_* block there), so there's one place to configure both.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app (accounts/tasks.py).
app.autodiscover_tasks()
