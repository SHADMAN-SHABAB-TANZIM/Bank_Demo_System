from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from accounts import ledger, notifications
from accounts.models import BankAccount, Transaction
from accounts.utils import generate_transaction_reference, log_action


class Command(BaseCommand):

    """
    Credits monthly interest to every ACTIVE savings account,
    using each account's own `interest_rate` (annual %).

    Interest is applied as balance * (annual_rate / 100 / 12),
    rounded to 2 decimal places. Accounts where the computed
    interest rounds to 0.00 are skipped (no zero-amount
    transactions are created).

    Intended to be run monthly, e.g. via cron:

        0 0 1 * *  cd /path/to/project && \
            .venv/bin/python manage.py credit_interest

    Usage:

        python manage.py credit_interest
        python manage.py credit_interest --dry-run
    """

    help = (
        "Credit monthly interest to all ACTIVE SAVINGS "
        "accounts based on each account's interest_rate."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be credited without saving anything.",
        )

    def handle(self, *args, **options):

        dry_run = options["dry_run"]

        accounts = BankAccount.objects.filter(
            status="ACTIVE",
            account_type="SAVINGS",
        )

        credited = 0
        skipped = 0

        for account in accounts:

            monthly_rate = account.interest_rate / Decimal("100") / Decimal("12")

            interest = (account.balance * monthly_rate).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

            if interest <= Decimal("0.00"):
                skipped += 1
                continue

            if dry_run:

                self.stdout.write(
                    f"[dry-run] {account.account_number}: "
                    f"would credit ৳{interest} "
                    f"(rate {account.interest_rate}%, "
                    f"balance ৳{account.balance})"
                )

                credited += 1
                continue

            with db_transaction.atomic():

                locked_account = (
                    BankAccount.objects
                    .select_for_update()
                    .get(id=account.id)
                )

                locked_account.balance += interest

                locked_account.save(
                    update_fields=["balance"],
                )

                txn = Transaction.objects.create(
                    account=locked_account,
                    destination_account=None,
                    transaction_type="INTEREST",
                    amount=interest,
                    balance_after=locked_account.balance,
                    reference=generate_transaction_reference(locked_account),
                    description=(
                        f"Monthly interest at "
                        f"{locked_account.interest_rate}% APR"
                    ),
                    status="COMPLETED",
                )

                log_action(
                    None,
                    "CREATE",
                    txn,
                    note="Credited by credit_interest command",
                )

                ledger.post_interest(txn, user=None)

                notifications.notify_transaction(txn)

            self.stdout.write(
                self.style.SUCCESS(
                    f"{account.account_number}: credited ৳{interest}"
                )
            )

            credited += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Credited: {credited}, skipped (zero interest): {skipped}."
            )
        )
