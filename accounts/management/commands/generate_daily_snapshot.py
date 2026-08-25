from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from accounts.models import (
    Customer,
    BankAccount,
    Transaction,
    DailySnapshot,
)


class Command(BaseCommand):

    """
    Rolls up today's system-wide stats into a DailySnapshot
    row, mirroring generate_daily_report / daily_report_view
    from the original Oracle project. update_or_create means
    it's safe to run more than once on the same day (e.g. if
    re-run after fixing something) - it will just refresh
    today's row rather than duplicate it.

    Intended to run daily via a scheduler, after business
    hours:

        python manage.py generate_daily_snapshot

    Usage:

        python manage.py generate_daily_snapshot
        python manage.py generate_daily_snapshot --date 2026-08-01
    """

    help = "Write today's (or a given date's) system-wide DailySnapshot."

    def add_arguments(self, parser):

        parser.add_argument(
            "--date",
            help="ISO date (YYYY-MM-DD) to snapshot as-of. Defaults to today.",
        )

    def handle(self, *args, **options):

        if options["date"]:

            from datetime import date as date_cls

            target_date = date_cls.fromisoformat(options["date"])

        else:

            target_date = timezone.localdate()

        customers_count = Customer.objects.count()

        accounts_count = BankAccount.objects.count()

        active_accounts = BankAccount.objects.filter(
            status="ACTIVE",
        ).count()

        inactive_accounts = accounts_count - active_accounts

        transactions_count = Transaction.objects.filter(
            created_at__date=target_date,
        ).count()

        total_deposits = (
            Transaction.objects
            .filter(
                created_at__date=target_date,
                transaction_type="DEPOSIT",
                status="COMPLETED",
            )
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

        total_withdrawals = (
            Transaction.objects
            .filter(
                created_at__date=target_date,
                transaction_type="WITHDRAW",
                status="COMPLETED",
            )
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

        total_balance = (
            BankAccount.objects
            .aggregate(total=Sum("balance"))["total"]
            or Decimal("0.00")
        )

        snapshot, created = DailySnapshot.objects.update_or_create(
            date=target_date,
            defaults={
                "customers_count": customers_count,
                "accounts_count": accounts_count,
                "active_accounts": active_accounts,
                "inactive_accounts": inactive_accounts,
                "transactions_count": transactions_count,
                "total_deposits": total_deposits,
                "total_withdrawals": total_withdrawals,
                "total_balance": total_balance,
            },
        )

        verb = "Created" if created else "Updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} snapshot for {target_date}: "
                f"{transactions_count} txns, "
                f"deposits ৳{total_deposits}, "
                f"withdrawals ৳{total_withdrawals}, "
                f"total balance ৳{total_balance}."
            )
        )
