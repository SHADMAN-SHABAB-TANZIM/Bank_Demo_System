from django.core.management.base import BaseCommand

from accounts.models import ChartOfAccount


ACCOUNTS = [
    ("1001", "Cash and Cash Equivalents", "ASSET"),
    ("1101", "Loans Receivable", "ASSET"),
    ("2001", "Customer Deposits - Savings", "LIABILITY"),
    ("2002", "Customer Deposits - Current", "LIABILITY"),
    ("4001", "Interest Income", "INCOME"),
    ("4002", "Fee Income", "INCOME"),
    ("5001", "Interest Expense", "EXPENSE"),
]


class Command(BaseCommand):

    """
    Creates the baseline Chart of Accounts the ledger posting
    functions in accounts.ledger rely on. Must be run once
    before any deposit/withdraw/transfer/interest/loan
    disbursement is posted to the ledger - those calls look
    up accounts by code and will fail with
    ChartOfAccount.DoesNotExist otherwise. Safe to re-run;
    only creates accounts that don't already exist.
    """

    help = "Create the baseline Chart of Accounts (idempotent)."

    def handle(self, *args, **options):

        created_count = 0

        for code, name, account_type in ACCOUNTS:

            account, created = ChartOfAccount.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "account_type": account_type,
                },
            )

            if created:
                created_count += 1
                self.stdout.write(f"Created: {account}")
            else:
                self.stdout.write(f"Already exists: {account}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_count} new account(s), "
                f"{len(ACCOUNTS) - created_count} already existed."
            )
        )
