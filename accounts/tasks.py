"""
Celery tasks - thin wrappers around the existing management
commands, so the exact same, already-tested logic runs whether
triggered by hand (`python manage.py credit_interest`), by
Windows Task Scheduler, or by celery beat in a Docker/Postgres/
Redis deployment. No business logic lives here.
"""

from celery import shared_task
from django.core.management import call_command


@shared_task
def credit_interest_task():
    call_command("credit_interest")


@shared_task
def run_standing_orders_task():
    call_command("run_standing_orders")


@shared_task
def generate_daily_snapshot_task():
    call_command("generate_daily_snapshot")


@shared_task
def mark_overdue_installments_task():
    call_command("mark_overdue_installments")
