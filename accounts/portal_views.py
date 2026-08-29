"""
Customer self-service portal - read-only views scoped to the
logged-in customer's own data. Staff never see these URLs (a
staff user has no `customer_profile`, so the guard below
rejects them); a portal customer never sees the staff URLs
(those all require Django model permissions a portal account
is never granted).
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from .models import BankAccount, Loan, Transaction
from .utils import amortization_schedule, calculate_emi


def portal_required(view_func):

    """
    Like @login_required, but also requires the user to have a
    CustomerPortalAccount - i.e. this is the customer-portal
    equivalent of @permission_required for staff views.
    """

    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):

        if not hasattr(request.user, "customer_profile"):
            raise PermissionDenied(
                "This page is only available to customer portal accounts."
            )

        return view_func(request, *args, **kwargs)

    return wrapped


@portal_required
def portal_dashboard(request):

    customer = request.user.customer_profile.customer

    accounts = customer.bank_accounts.all().order_by("account_number")

    total_balance = sum((a.balance for a in accounts), start=0)

    recent_transactions = (
        Transaction.objects
        .filter(account__customer=customer)
        .select_related("account", "destination_account")
        .order_by("-created_at", "-id")[:10]
    )

    return render(
        request,
        "accounts/portal_dashboard.html",
        {
            "customer": customer,
            "accounts": accounts,
            "total_balance": total_balance,
            "recent_transactions": recent_transactions,
        },
    )


@portal_required
def portal_account_detail(request, account_id):

    customer = request.user.customer_profile.customer

    account = get_object_or_404(
        BankAccount, id=account_id, customer=customer,
    )

    transactions = (
        Transaction.objects
        .filter(account=account)
        .order_by("-created_at", "-id")[:100]
    )

    return render(
        request,
        "accounts/portal_account_detail.html",
        {
            "account": account,
            "transactions": transactions,
        },
    )


@portal_required
def portal_transaction_list(request):

    customer = request.user.customer_profile.customer

    transactions = (
        Transaction.objects
        .filter(account__customer=customer)
        .select_related("account", "destination_account")
        .order_by("-created_at", "-id")
    )

    return render(
        request,
        "accounts/portal_transaction_list.html",
        {
            "transactions": transactions,
        },
    )


@portal_required
def portal_loan_list(request):

    customer = request.user.customer_profile.customer

    loans = (
        Loan.objects
        .filter(account__customer=customer)
        .select_related("account")
        .order_by("-created_at")
    )

    return render(
        request,
        "accounts/portal_loan_list.html",
        {
            "loans": loans,
        },
    )


@portal_required
def portal_loan_detail(request, loan_id):

    customer = request.user.customer_profile.customer

    loan = get_object_or_404(
        Loan.objects.select_related("account"),
        id=loan_id, account__customer=customer,
    )

    emi = calculate_emi(loan.principal, loan.annual_rate, loan.months)

    installments = loan.installments.all()

    return render(
        request,
        "accounts/portal_loan_detail.html",
        {
            "loan": loan,
            "emi": emi,
            "installments": installments,
        },
    )
