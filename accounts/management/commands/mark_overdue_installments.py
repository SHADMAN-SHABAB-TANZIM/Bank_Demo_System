from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import LoanInstallment


# One-time late penalty applied when an installment first goes
# overdue: 2% of the installment's total_due. Applied only on
# the PENDING -> OVERDUE transition (checked via status, so
# running this command daily never double-charges the penalty).
PENALTY_RATE_PERCENT = Decimal("2.00")


class Command(BaseCommand):

    """
    Finds every loan installment that's still PENDING but past
    its due_date, marks it OVERDUE, and applies a one-time late
    penalty (2% of the installment amount). Safe to run daily;
    already-OVERDUE installments are left alone so the penalty
    is never applied twice.

    Usage:

        python manage.py mark_overdue_installments
        python manage.py mark_overdue_installments --dry-run
    """

    help = "Mark past-due loan installments as OVERDUE and apply a one-time late penalty."

    def add_arguments(self, parser):

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without saving anything.",
        )

    def handle(self, *args, **options):

        dry_run = options["dry_run"]

        today = timezone.localdate()

        overdue_qs = LoanInstallment.objects.filter(
            status="PENDING",
            due_date__lt=today,
        ).select_related("loan", "loan__account")

        count = 0

        for installment in overdue_qs:

            penalty = (
                installment.total_due * PENALTY_RATE_PERCENT / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if dry_run:

                self.stdout.write(
                    f"[dry-run] Loan #{installment.loan_id} installment "
                    f"{installment.installment_no} (due {installment.due_date}) "
                    f"-> OVERDUE, penalty ৳{penalty}"
                )

                count += 1
                continue

            installment.status = "OVERDUE"
            installment.penalty_amount = penalty
            installment.save(update_fields=["status", "penalty_amount"])

            self.stdout.write(
                self.style.WARNING(
                    f"Loan #{installment.loan_id} installment "
                    f"{installment.installment_no}: marked OVERDUE, "
                    f"penalty ৳{penalty}"
                )
            )

            count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done. {count} installment(s) processed.")
        )
