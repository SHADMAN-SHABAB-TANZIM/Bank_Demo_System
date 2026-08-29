from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4


def generate_transaction_reference(account):

    """
    Generates a unique transaction reference.

    Example:

    TXN-ACC10001-A1B2C3D4
    """

    from .models import Transaction

    while True:

        reference = (
            f"TXN-{account.account_number}-"
            f"{uuid4().hex[:8].upper()}"
        )

        if not Transaction.objects.filter(
            reference=reference,
        ).exists():

            return reference


def log_action(request, action, instance, note="", object_id=None):

    """
    Records an AuditLog entry for a create/update/delete
    action performed by the currently logged-in user.

    `request` may be None (e.g. when called from a
    management command); in that case the log entry is
    recorded with no associated user.

    `object_id` should be passed explicitly when logging a
    DELETE, since Django sets instance.pk to None after a
    successful .delete() call.
    """

    from .models import AuditLog

    user = None

    if request is not None and request.user.is_authenticated:
        user = request.user

    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(object_id if object_id is not None else instance.pk),
        object_repr=str(instance)[:255],
        note=note,
    )


def calculate_emi(principal, annual_rate, months):

    """
    Standard reducing-balance EMI formula, ported from the
    calc_emi PL/SQL function:

        EMI = P * r * (1+r)^n / ((1+r)^n - 1)

    where r is the monthly interest rate. Falls back to a
    straight-line division when the rate is zero (matches the
    original function's IF v_monthly_rate = 0 branch).
    """

    principal = Decimal(principal)
    annual_rate = Decimal(annual_rate)
    months = int(months)

    monthly_rate = (annual_rate / Decimal("12")) / Decimal("100")

    if monthly_rate == 0:

        emi = principal / Decimal(months)

    else:

        factor = (1 + monthly_rate) ** months

        emi = (principal * monthly_rate * factor) / (factor - 1)

    return emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def add_months(source_date, months):

    """
    Adds `months` calendar months to a date, clamping the day
    to the last valid day of the resulting month (e.g. 31 Jan
    + 1 month -> 28/29 Feb, not an invalid date). Avoids
    pulling in python-dateutil for one calculation.
    """

    import calendar

    month_index = source_date.month - 1 + months
    year = source_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source_date.day, calendar.monthrange(year, month)[1])

    return source_date.replace(year=year, month=month, day=day)


def amortization_schedule(principal, annual_rate, months, start_date):

    """
    Full month-by-month breakdown of a loan: opening balance,
    EMI, interest portion, principal portion, closing balance.
    Not persisted - computed on demand for display.
    """

    principal = Decimal(principal)
    annual_rate = Decimal(annual_rate)
    months = int(months)

    monthly_rate = (annual_rate / Decimal("12")) / Decimal("100")
    emi = calculate_emi(principal, annual_rate, months)

    schedule = []
    balance = principal

    for i in range(1, months + 1):

        interest = (balance * monthly_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )

        principal_portion = emi - interest

        if i == months:
            # Absorb any rounding drift into the final
            # installment so the schedule closes to exactly 0.
            principal_portion = balance
            this_emi = principal_portion + interest
        else:
            this_emi = emi

        closing = balance - principal_portion

        schedule.append({
            "month_no": i,
            "date": add_months(start_date, i - 1),
            "opening_balance": balance,
            "emi": this_emi,
            "interest": interest,
            "principal": principal_portion,
            "closing_balance": closing if closing > 0 else Decimal("0.00"),
        })

        balance = closing

    return schedule


def filter_transactions(request, queryset):

    """
    Applies the same search/type/status/date-range filters
    used by transaction_list to any Transaction queryset -
    shared with the CSV export view so both stay in sync.
    """

    from django.db.models import Q

    search = request.GET.get("search", "").strip()
    transaction_type = request.GET.get("transaction_type", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search:

        queryset = queryset.filter(
            Q(reference__icontains=search)
            | Q(account__account_number__icontains=search)
            | Q(account__customer__name__icontains=search)
        )

    if transaction_type:

        queryset = queryset.filter(transaction_type=transaction_type)

    if status:

        queryset = queryset.filter(status=status)

    if date_from:

        queryset = queryset.filter(created_at__date__gte=date_from)

    if date_to:

        queryset = queryset.filter(created_at__date__lte=date_to)

    return queryset


def calculate_fee(transaction_type, amount):

    """
    Looks up the first active FeeRule for `transaction_type`
    and computes the fee for `amount`. Returns Decimal("0.00")
    if no active rule matches - fees are opt-in, so this is
    the safe default when nothing's been configured.
    """

    from .models import FeeRule

    rule = (
        FeeRule.objects
        .filter(transaction_type=transaction_type, is_active=True)
        .first()
    )

    if rule is None:
        return Decimal("0.00")

    if rule.fee_type == "FLAT":
        fee = rule.amount
    else:
        fee = (amount * rule.amount / Decimal("100"))

    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
