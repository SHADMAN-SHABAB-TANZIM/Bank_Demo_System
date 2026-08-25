from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone

from accounts.models import BankAccount, StandingOrder, Transaction
from accounts.utils import generate_transaction_reference, log_action


FREQUENCY_DELTAS = {
    "DAILY": timedelta(days=1),
    "WEEKLY": timedelta(weeks=1),
    "MONTHLY": timedelta(days=30),
}


class Command(BaseCommand):

    """
    Executes every active StandingOrder whose next_run_date
    has arrived, as a real TRANSFER transaction between the
    two accounts (reusing the same locking + balance-check
    logic as a manual transfer). Advances next_run_date by
    the order's frequency afterwards.

    An order that fails validation (inactive/closed account,
    insufficient balance) is skipped and left as-is so it can
    be retried on the next run; it is reported, not silently
    dropped.

    Intended to run daily via cron:

        0 1 * * *  cd /path/to/project && \
            .venv/bin/python manage.py run_standing_orders

    Usage:

        python manage.py run_standing_orders
        python manage.py run_standing_orders --dry-run
    """

    help = "Execute all due standing orders (recurring transfers)."

    def add_arguments(self, parser):

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would run without executing anything.",
        )

    def handle(self, *args, **options):

        dry_run = options["dry_run"]

        today = timezone.localdate()

        due_orders = StandingOrder.objects.filter(
            is_active=True,
            next_run_date__lte=today,
        ).select_related("account", "destination_account")

        executed = 0
        failed = 0

        for order in due_orders:

            if dry_run:

                self.stdout.write(
                    f"[dry-run] would run order #{order.id}: "
                    f"{order.account.account_number} -> "
                    f"{order.destination_account.account_number} "
                    f"৳{order.amount}"
                )

                executed += 1
                continue

            try:

                with db_transaction.atomic():

                    source = (
                        BankAccount.objects
                        .select_for_update()
                        .get(id=order.account_id)
                    )

                    destination = (
                        BankAccount.objects
                        .select_for_update()
                        .get(id=order.destination_account_id)
                    )

                    if source.status != "ACTIVE":
                        raise ValueError(
                            f"Source account {source.account_number} "
                            "is not active."
                        )

                    if destination.status != "ACTIVE":
                        raise ValueError(
                            f"Destination account "
                            f"{destination.account_number} is not active."
                        )

                    if source.balance < order.amount:
                        raise ValueError(
                            f"Insufficient balance on "
                            f"{source.account_number}."
                        )

                    source.balance -= order.amount
                    destination.balance += order.amount

                    source.save(update_fields=["balance"])
                    destination.save(update_fields=["balance"])

                    txn = Transaction.objects.create(
                        account=source,
                        destination_account=destination,
                        transaction_type="TRANSFER",
                        amount=order.amount,
                        balance_after=source.balance,
                        reference=generate_transaction_reference(source),
                        description=(
                            order.description
                            or f"Standing order #{order.id}"
                        ),
                        status="COMPLETED",
                    )

                    delta = FREQUENCY_DELTAS[order.frequency]
                    order.next_run_date = order.next_run_date + delta
                    order.save(update_fields=["next_run_date"])

                    log_action(
                        None,
                        "CREATE",
                        txn,
                        note=f"Executed by standing order #{order.id}",
                    )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Order #{order.id}: transferred ৳{order.amount} "
                        f"{order.account.account_number} -> "
                        f"{order.destination_account.account_number}. "
                        f"Next run: {order.next_run_date}."
                    )
                )

                executed += 1

            except ValueError as exc:

                self.stdout.write(
                    self.style.WARNING(
                        f"Order #{order.id}: skipped ({exc})"
                    )
                )

                failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Executed: {executed}, skipped/failed: {failed}."
            )
        )
