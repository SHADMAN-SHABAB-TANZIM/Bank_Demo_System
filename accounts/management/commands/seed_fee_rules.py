from django.core.management.base import BaseCommand

from accounts.models import FeeRule


RULES = [
    ("Standard Withdrawal Fee", "WITHDRAW", "FLAT", "5.00"),
    ("Standard Transfer Fee", "TRANSFER", "PERCENTAGE", "0.50"),
]


class Command(BaseCommand):

    """
    Creates sensible default FeeRule rows - a flat ৳5 withdrawal
    fee and a 0.5% transfer fee - but leaves them INACTIVE
    (is_active=False), so running this command never changes
    existing behavior. A manager has to explicitly activate a
    rule (via the Fee Rules page or /admin/) to start charging
    it. Safe to re-run; only creates rules that don't already
    exist by name.
    """

    help = "Create default (inactive) fee rules for withdrawals and transfers."

    def handle(self, *args, **options):

        created_count = 0

        for name, transaction_type, fee_type, amount in RULES:

            rule, created = FeeRule.objects.get_or_create(
                name=name,
                defaults={
                    "transaction_type": transaction_type,
                    "fee_type": fee_type,
                    "amount": amount,
                    "is_active": False,
                },
            )

            if created:
                created_count += 1
                self.stdout.write(f"Created (inactive): {rule}")
            else:
                self.stdout.write(f"Already exists: {rule}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_count} new rule(s). "
                "All start inactive - activate from the Fee Rules "
                "page when ready."
            )
        )
