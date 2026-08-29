"""
Rule-based fraud detection - a first-pass "risk scoring" layer
of the kind most real banking fraud systems use before (or
instead of) a full ML model: a handful of independent checks,
each contributing points to a risk score, with anything over
the alert threshold surfaced for staff review.

Design principle: flagging is purely additive. It NEVER blocks,
delays, or alters the underlying transaction - it only creates
a FraudAlert for later human review. A false positive here costs
a staff member a few minutes; a false block costs a customer a
failed transaction. Given this is a first-pass rule engine (not
a tuned model), the safer default is "flag, don't block".
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone


ALERT_THRESHOLD = 50

LARGE_AMOUNT_THRESHOLD = Decimal("50000.00")

VELOCITY_WINDOW_MINUTES = 5
VELOCITY_COUNT_THRESHOLD = 3

STRUCTURING_REPORTING_THRESHOLD = Decimal("10000.00")
STRUCTURING_WINDOW_HOURS = 24
STRUCTURING_COUNT_THRESHOLD = 3

NEW_ACCOUNT_WINDOW_HOURS = 24
NEW_ACCOUNT_AMOUNT_THRESHOLD = Decimal("20000.00")

OFF_HOURS_START = 0   # midnight
OFF_HOURS_END = 5     # 5am
OFF_HOURS_AMOUNT_THRESHOLD = Decimal("20000.00")


def _check_large_amount(transaction):

    if transaction.amount >= LARGE_AMOUNT_THRESHOLD:

        return (
            "large_amount", 60,
            f"Amount \u09f3{transaction.amount} exceeds the "
            f"\u09f3{LARGE_AMOUNT_THRESHOLD} large-transaction threshold.",
        )

    return None


def _check_velocity(transaction):

    from .models import Transaction

    window_start = transaction.created_at - timedelta(
        minutes=VELOCITY_WINDOW_MINUTES,
    )

    recent_count = (
        Transaction.objects
        .filter(
            account=transaction.account,
            created_at__gte=window_start,
            created_at__lte=transaction.created_at,
        )
        .exclude(id=transaction.id)
        .count()
    )

    if recent_count >= VELOCITY_COUNT_THRESHOLD - 1:

        return (
            "velocity", 30,
            f"{recent_count + 1} transactions on "
            f"{transaction.account.account_number} within "
            f"{VELOCITY_WINDOW_MINUTES} minutes.",
        )

    return None


def _check_structuring(transaction):

    """
    Multiple transactions just under a reporting threshold in
    a short window - a classic pattern for evading reporting
    requirements (deliberately breaking a large sum into
    smaller pieces).
    """

    from .models import Transaction

    lower_bound = STRUCTURING_REPORTING_THRESHOLD * Decimal("0.9")

    if not (lower_bound <= transaction.amount < STRUCTURING_REPORTING_THRESHOLD):
        return None

    window_start = transaction.created_at - timedelta(
        hours=STRUCTURING_WINDOW_HOURS,
    )

    similar_count = (
        Transaction.objects
        .filter(
            account=transaction.account,
            created_at__gte=window_start,
            created_at__lte=transaction.created_at,
            amount__gte=lower_bound,
            amount__lt=STRUCTURING_REPORTING_THRESHOLD,
        )
        .exclude(id=transaction.id)
        .count()
    )

    if similar_count >= STRUCTURING_COUNT_THRESHOLD - 1:

        return (
            "structuring", 50,
            f"{similar_count + 1} transactions just under the "
            f"\u09f3{STRUCTURING_REPORTING_THRESHOLD} reporting "
            f"threshold within {STRUCTURING_WINDOW_HOURS}h - "
            "possible structuring.",
        )

    return None


def _check_new_account_large_transaction(transaction):

    account = transaction.account

    account_age = transaction.created_at - account.created_at

    if (
        account_age <= timedelta(hours=NEW_ACCOUNT_WINDOW_HOURS)
        and transaction.amount >= NEW_ACCOUNT_AMOUNT_THRESHOLD
    ):

        return (
            "new_account_large_transaction", 35,
            f"Account {account.account_number} is "
            f"{account_age} old and just moved "
            f"\u09f3{transaction.amount}.",
        )

    return None


def _check_off_hours(transaction):

    local_time = timezone.localtime(transaction.created_at)

    if (
        OFF_HOURS_START <= local_time.hour < OFF_HOURS_END
        and transaction.amount >= OFF_HOURS_AMOUNT_THRESHOLD
    ):

        return (
            "off_hours", 15,
            f"Large transaction (\u09f3{transaction.amount}) posted "
            f"at {local_time.strftime('%H:%M')}.",
        )

    return None


RULES = [
    _check_large_amount,
    _check_velocity,
    _check_structuring,
    _check_new_account_large_transaction,
    _check_off_hours,
]


def evaluate_transaction(transaction):

    """
    Runs every rule against `transaction`, and creates a
    FraudAlert if the combined score meets ALERT_THRESHOLD.
    Returns the created FraudAlert, or None if nothing matched
    or the score fell short. Safe to call on every transaction
    unconditionally - the common case (no rules match) is cheap
    and does nothing.
    """

    from .models import FraudAlert

    matches = []
    total_score = 0

    for rule in RULES:

        result = rule(transaction)

        if result is not None:

            rule_name, weight, explanation = result
            matches.append(explanation)
            total_score += weight

    if total_score < ALERT_THRESHOLD:
        return None

    total_score = min(total_score, 100)

    return FraudAlert.objects.create(
        transaction=transaction,
        reason="\n".join(matches),
        risk_score=total_score,
        status="PENDING_REVIEW",
    )
