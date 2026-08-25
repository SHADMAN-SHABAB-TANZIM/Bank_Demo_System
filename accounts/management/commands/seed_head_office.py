from django.core.management.base import BaseCommand

from accounts.models import Branch, BankAccount, Customer


class Command(BaseCommand):

    """
    One-time setup helper: creates a default "Head Office"
    branch (code HQ-01) and assigns it to any existing
    customers/accounts that don't already have a branch -
    i.e. everything created before branches existed. Safe to
    re-run; only touches records with branch=None.
    """

    help = (
        "Create a default Head Office branch and backfill it "
        "onto any customers/accounts created before branches "
        "existed."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--code",
            default="HQ-01",
            help="Branch code to create/use (default: HQ-01).",
        )

        parser.add_argument(
            "--name",
            default="Head Office",
            help="Branch name (default: 'Head Office').",
        )

    def handle(self, *args, **options):

        branch, created = Branch.objects.get_or_create(
            code=options["code"],
            defaults={"name": options["name"]},
        )

        verb = "Created" if created else "Using existing"

        self.stdout.write(f"{verb} branch: {branch}")

        customers_updated = Customer.objects.filter(
            branch__isnull=True,
        ).update(branch=branch)

        accounts_updated = BankAccount.objects.filter(
            branch__isnull=True,
        ).update(branch=branch)

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned {customers_updated} customer(s) and "
                f"{accounts_updated} account(s) to '{branch}'."
            )
        )
