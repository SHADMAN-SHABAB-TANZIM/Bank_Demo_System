from decimal import Decimal

import csv

from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
    permission_required,
)
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.utils import timezone

from . import ledger
from . import fraud
from . import notifications
from .reversal import reverse_transaction, ReversalError

from .utils import (
    generate_transaction_reference,
    log_action,
    calculate_emi,
    amortization_schedule,
    filter_transactions,
    calculate_fee,
)

from .branch_scope import (
    get_employee_profile,
    get_user_branch,
    scope_to_branch,
)

from .forms import (
    CustomerForm,
    BankAccountForm,
    TransactionForm,
    StandingOrderForm,
    LoanForm,
    BranchForm,
    EmployeeProfileForm,
    FeeRuleForm,
)

from .models import (
    Customer,
    BankAccount,
    Transaction,
    StandingOrder,
    AuditLog,
    Loan,
    LoanInstallment,
    DailySnapshot,
    Branch,
    EmployeeProfile,
    ChartOfAccount,
    FeeRule,
    CustomerPortalAccount,
    FraudAlert,
)


# ============================================================
# HOME / DASHBOARD
# ============================================================

@login_required
def home(request):

    if hasattr(request.user, "customer_profile"):
        return redirect("portal_dashboard")

    # --------------------------------------------------------
    # Dashboard statistics (branch-scoped: a Branch Manager /
    # Teller / Bank Officer / Loan Officer only sees their own
    # branch's numbers; Super Admin / System Admin / Auditor
    # and any user without an EmployeeProfile see everything)
    # --------------------------------------------------------

    customers_qs = scope_to_branch(Customer.objects.all(), request.user)
    accounts_qs = scope_to_branch(BankAccount.objects.all(), request.user)
    transactions_qs = scope_to_branch(
        Transaction.objects.all(), request.user, branch_field="account__branch",
    )

    customers_count = customers_qs.count()

    accounts_count = accounts_qs.count()

    total_balance = (
        accounts_qs.aggregate(
            total=Sum("balance")
        )["total"]
        or Decimal("0.00")
    )

    transactions_count = transactions_qs.count()

    # --------------------------------------------------------
    # Financial activity
    # --------------------------------------------------------

    deposits_qs = transactions_qs.filter(
        transaction_type="DEPOSIT",
        status="COMPLETED",
    )

    withdrawals_qs = transactions_qs.filter(
        transaction_type="WITHDRAW",
        status="COMPLETED",
    )

    transfers_qs = transactions_qs.filter(
        transaction_type="TRANSFER",
        status="COMPLETED",
    )

    total_deposits = (
        deposits_qs.aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    deposits_count = deposits_qs.count()

    total_withdrawals = (
        withdrawals_qs.aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    withdrawals_count = withdrawals_qs.count()

    total_transfers = (
        transfers_qs.aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    transfers_count = transfers_qs.count()

    # --------------------------------------------------------
    # Recent transactions
    # --------------------------------------------------------

    recent_transactions = (
        transactions_qs
        .select_related(
            "account",
            "account__customer",
            "destination_account",
            "destination_account__customer",
        )
        .order_by(
            "-created_at",
            "-id",
        )[:5]
    )

    # --------------------------------------------------------
    # Trend data for the dashboard chart (last 30 days of
    # DailySnapshot, written by the generate_daily_snapshot
    # management command)
    # --------------------------------------------------------

    snapshots = (
        DailySnapshot.objects
        .order_by("date")[:30]
    )

    snapshot_labels = [
        s.date.strftime("%b %d") for s in snapshots
    ]
    snapshot_deposits = [
        float(s.total_deposits) for s in snapshots
    ]
    snapshot_withdrawals = [
        float(s.total_withdrawals) for s in snapshots
    ]
    snapshot_balance = [
        float(s.total_balance) for s in snapshots
    ]

    # --------------------------------------------------------
    # "Needs Attention" panel - pending fraud alerts and
    # overdue loan installments, branch-scoped like everything
    # else. Only queried if the user can actually act on them,
    # so a Teller with no fraud-review permission doesn't see
    # a panel they can't do anything about.
    # --------------------------------------------------------

    pending_fraud_alerts = None
    overdue_installments_count = 0

    if request.user.has_perm("accounts.view_fraudalert"):

        pending_fraud_alerts = (
            scope_to_branch(
                FraudAlert.objects.filter(status="PENDING_REVIEW"),
                request.user,
                branch_field="transaction__account__branch",
            )
            .select_related(
                "transaction", "transaction__account",
                "transaction__account__customer",
            )
            .order_by("-risk_score")[:5]
        )

        pending_fraud_alerts_count = pending_fraud_alerts.count()

    else:

        pending_fraud_alerts_count = 0

    if request.user.has_perm("accounts.view_loan"):

        overdue_installments_count = (
            scope_to_branch(
                LoanInstallment.objects.filter(status="OVERDUE"),
                request.user,
                branch_field="loan__account__branch",
            )
            .count()
        )

    # --------------------------------------------------------
    # Dashboard
    # --------------------------------------------------------

    return render(
        request,
        "accounts/home.html",
        {
            "customers_count": customers_count,
            "accounts_count": accounts_count,
            "total_balance": total_balance,
            "transactions_count": transactions_count,

            "total_deposits": total_deposits,
            "deposits_count": deposits_count,
            "total_withdrawals": total_withdrawals,
            "withdrawals_count": withdrawals_count,
            "total_transfers": total_transfers,
            "transfers_count": transfers_count,

            "recent_transactions": recent_transactions,

            "has_snapshots": snapshots.exists(),
            "snapshot_labels": snapshot_labels,
            "snapshot_deposits": snapshot_deposits,
            "snapshot_withdrawals": snapshot_withdrawals,
            "snapshot_balance": snapshot_balance,

            "pending_fraud_alerts": pending_fraud_alerts,
            "pending_fraud_alerts_count": pending_fraud_alerts_count,
            "overdue_installments_count": overdue_installments_count,
        },
    )

# ============================================================
# CUSTOMERS
# ============================================================

@login_required
@permission_required(
    "accounts.view_customer",
    raise_exception=True,
)
def customer_list(request):

    search = request.GET.get(
        "search",
        "",
    ).strip()

    status = request.GET.get(
        "status",
        "active",
    ).strip()

    customers = (
        Customer.objects
        .all()
        .order_by("-created_at")
    )

    customers = scope_to_branch(customers, request.user)

    # --------------------------------------------------------
    # Active / inactive filter (defaults to active only, so
    # deactivated customers stay out of the way without being
    # deleted)
    # --------------------------------------------------------

    if status == "active":

        customers = customers.filter(is_active=True)

    elif status == "inactive":

        customers = customers.filter(is_active=False)

    # status == "all" -> no filter applied

    # --------------------------------------------------------
    # Search customers
    # --------------------------------------------------------

    if search:

        customers = customers.filter(
            Q(name__icontains=search)
            | Q(email__icontains=search)
        )

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    paginator = Paginator(
        customers,
        10,
    )

    page_number = request.GET.get(
        "page",
    )

    page_obj = paginator.get_page(
        page_number,
    )

    return render(
        request,
        "accounts/customer_list.html",
        {
            "customers": page_obj,
            "status": status,
            "page_obj": page_obj,
            "search": search,
        },
    )


@login_required
@permission_required(
    "accounts.add_customer",
    raise_exception=True,
)
def customer_create(request):

    if request.method == "POST":

        form = CustomerForm(
            request.POST,
        )

        if form.is_valid():

            customer = form.save()

            log_action(request, "CREATE", customer)

            messages.success(
                request,
                f"Customer '{customer.name}' created.",
            )

            return redirect(
                "customer_list",
            )

    else:

        form = CustomerForm()

    return render(
        request,
        "accounts/customer_form.html",
        {
            "form": form,
        },
    )


@login_required
@permission_required(
    "accounts.view_customer",
    raise_exception=True,
)
def customer_detail(
    request,
    customer_id,
):

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    return render(
        request,
        "accounts/customer_detail.html",
        {
            "customer": customer,
        },
    )


@login_required
@permission_required(
    "accounts.change_customer",
    raise_exception=True,
)
def customer_update(
    request,
    customer_id,
):

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if request.method == "POST":

        form = CustomerForm(
            request.POST,
            instance=customer,
        )

        if form.is_valid():

            customer = form.save()

            log_action(request, "UPDATE", customer)

            messages.success(
                request,
                f"Customer '{customer.name}' updated.",
            )

            return redirect(
                "customer_detail",
                customer_id=customer.id,
            )

    else:

        form = CustomerForm(
            instance=customer,
        )

    return render(
        request,
        "accounts/customer_form.html",
        {
            "form": form,
            "customer": customer,
        },
    )


@login_required
@permission_required(
    "accounts.delete_customer",
    raise_exception=True,
)
def customer_delete(
    request,
    customer_id,
):

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if request.method == "POST":

        try:

            customer_id_str = str(customer.id)
            customer_name = customer.name

            customer.delete()

            log_action(
                request,
                "DELETE",
                customer,
                object_id=customer_id_str,
            )

            messages.success(
                request,
                f"Customer '{customer_name}' deleted.",
            )

        except ProtectedError:

            return render(
                request,
                "accounts/customer_confirm_delete.html",
                {
                    "customer": customer,
                    "error": (
                        "This customer cannot be deleted because "
                        "one or more of their accounts has "
                        "transaction history."
                    ),
                },
            )

        return redirect(
            "customer_list",
        )

    return render(
        request,
        "accounts/customer_confirm_delete.html",
        {
            "customer": customer,
        },
    )


@login_required
@permission_required(
    "accounts.change_customer",
    raise_exception=True,
)
def customer_deactivate(
    request,
    customer_id,
):

    """
    Marks a customer inactive instead of deleting them,
    mirroring the Oracle trg_prevent_delete trigger, which
    blocks DELETE on customers outright and pushes staff
    toward a soft-deactivate workflow. Unlike hard delete,
    this always succeeds - there's no dependent-record
    conflict to worry about.
    """

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if request.method == "POST":

        customer.is_active = False

        customer.save(
            update_fields=["is_active"],
        )

        log_action(
            request,
            "UPDATE",
            customer,
            note="Deactivated",
        )

        messages.warning(
            request,
            f"Customer '{customer.name}' deactivated.",
        )

        return redirect(
            "customer_detail",
            customer_id=customer.id,
        )

    return render(
        request,
        "accounts/customer_confirm_deactivate.html",
        {
            "customer": customer,
        },
    )


@login_required
@permission_required(
    "accounts.change_customer",
    raise_exception=True,
)
def customer_reactivate(
    request,
    customer_id,
):

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if request.method == "POST":

        customer.is_active = True

        customer.save(
            update_fields=["is_active"],
        )

        log_action(
            request,
            "UPDATE",
            customer,
            note="Reactivated",
        )

        messages.success(
            request,
            f"Customer '{customer.name}' reactivated.",
        )

    return redirect(
        "customer_detail",
        customer_id=customer.id,
    )


# ============================================================
# BANK ACCOUNTS
# ============================================================

@login_required
@permission_required(
    "accounts.view_bankaccount",
    raise_exception=True,
)
def bank_account_list(request):

    # ============================================================
    # GET FILTER VALUES
    # ============================================================

    search = request.GET.get(
        "search",
        "",
    ).strip()

    account_type = request.GET.get(
        "account_type",
        "",
    ).strip()

    status = request.GET.get(
        "status",
        "",
    ).strip()

    # ============================================================
    # BASE QUERYSET
    # ============================================================

    accounts = (
        BankAccount.objects
        .select_related("customer")
        .all()
        .order_by("-created_at")
    )

    accounts = scope_to_branch(accounts, request.user)

    # ============================================================
    # SEARCH
    # ============================================================

    if search:

        accounts = accounts.filter(
            Q(account_number__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(customer__email__icontains=search)
        )

    # ============================================================
    # ACCOUNT TYPE FILTER
    # ============================================================

    if account_type:

        accounts = accounts.filter(
            account_type=account_type,
        )

    # ============================================================
    # STATUS FILTER
    # ============================================================

    if status:

        accounts = accounts.filter(
            status=status,
        )

    # ============================================================
    # PAGINATION
    # ============================================================

    paginator = Paginator(
        accounts,
        10,
    )

    page_number = request.GET.get(
        "page",
    )

    page_obj = paginator.get_page(
        page_number,
    )

    # ============================================================
    # RENDER
    # ============================================================

    return render(
        request,
        "accounts/bank_account_list.html",
        {
            "bank_accounts": page_obj,
            "accounts": page_obj,
            "page_obj": page_obj,

            "search": search,
            "account_type": account_type,
            "status": status,
        },
    )
@login_required
@permission_required(
    "accounts.add_bankaccount",
    raise_exception=True,
)
def bank_account_create(request):

    if request.method == "POST":

        form = BankAccountForm(
            request.POST,
        )

        if form.is_valid():

            account = form.save()

            log_action(request, "CREATE", account)

            messages.success(
                request,
                f"Account '{account.account_number}' created.",
            )

            return redirect(
                "bank_account_list",
            )

    else:

        form = BankAccountForm()

    return render(
        request,
        "accounts/bank_account_form.html",
        {
            "form": form,
            "title": "Add New Bank Account",
        },
    )


# ============================================================
# BANK ACCOUNT DETAIL / ACCOUNT STATEMENT
# ============================================================

@login_required
@permission_required(
    "accounts.view_bankaccount",
    raise_exception=True,
)
def bank_account_detail(
    request,
    account_id,
):

    account = get_object_or_404(
        BankAccount.objects.select_related(
            "customer",
        ),
        id=account_id,
    )

    # --------------------------------------------------------
    # Get filters
    # --------------------------------------------------------

    search = request.GET.get(
        "search",
        "",
    ).strip()

    transaction_type = request.GET.get(
        "transaction_type",
        "",
    ).strip()

    status = request.GET.get(
        "status",
        "",
    ).strip()

    date_from = request.GET.get(
        "date_from",
        "",
    ).strip()

    date_to = request.GET.get(
        "date_to",
        "",
    ).strip()

    # --------------------------------------------------------
    # Account transactions
    # --------------------------------------------------------

    transactions = (
        Transaction.objects
        .filter(
            account=account,
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    # --------------------------------------------------------
    # Search by reference
    # --------------------------------------------------------

    if search:

        transactions = transactions.filter(
            reference__icontains=search,
        )

    # --------------------------------------------------------
    # Filter by transaction type
    # --------------------------------------------------------

    if transaction_type:

        transactions = transactions.filter(
            transaction_type=transaction_type,
        )

    # --------------------------------------------------------
    # Filter by status
    # --------------------------------------------------------

    if status:

        transactions = transactions.filter(
            status=status,
        )

    # --------------------------------------------------------
    # Filter by date
    # --------------------------------------------------------

    if date_from:

        transactions = transactions.filter(
            created_at__date__gte=date_from,
        )

    if date_to:

        transactions = transactions.filter(
            created_at__date__lte=date_to,
        )

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    paginator = Paginator(
        transactions,
        10,
    )

    page_number = request.GET.get(
        "page",
    )

    page_obj = paginator.get_page(
        page_number,
    )

    return render(
        request,
        "accounts/bank_account_detail.html",
        {
            "account": account,

            "transactions": page_obj,
            "page_obj": page_obj,

            "search": search,
            "transaction_type": transaction_type,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@login_required
@permission_required(
    "accounts.change_bankaccount",
    raise_exception=True,
)
def bank_account_update(
    request,
    account_id,
):

    account = get_object_or_404(
        BankAccount,
        id=account_id,
    )

    if request.method == "POST":

        form = BankAccountForm(
            request.POST,
            instance=account,
        )

        if form.is_valid():

            account = form.save()

            log_action(request, "UPDATE", account)

            messages.success(
                request,
                f"Account '{account.account_number}' updated.",
            )

            return redirect(
                "bank_account_detail",
                account_id=account.id,
            )

    else:

        form = BankAccountForm(
            instance=account,
        )

    return render(
        request,
        "accounts/bank_account_form.html",
        {
            "form": form,
            "account": account,
            "title": "Edit Bank Account",
        },
    )


@login_required
@permission_required(
    "accounts.delete_bankaccount",
    raise_exception=True,
)
def bank_account_delete(
    request,
    account_id,
):

    account = get_object_or_404(
        BankAccount,
        id=account_id,
    )

    if request.method == "POST":

        try:

            account_id_str = str(account.id)
            account_number = account.account_number

            account.delete()

            log_action(
                request,
                "DELETE",
                account,
                object_id=account_id_str,
            )

            messages.success(
                request,
                f"Account '{account_number}' deleted.",
            )

        except ProtectedError:

            return render(
                request,
                "accounts/bank_account_confirm_delete.html",
                {
                    "account": account,
                    "error": (
                        "This account cannot be deleted because "
                        "it has transaction history."
                    ),
                },
            )

        return redirect(
            "bank_account_list",
        )

    return render(
        request,
        "accounts/bank_account_confirm_delete.html",
        {
            "account": account,
        },
    )


# ============================================================
# TRANSACTION REFERENCE GENERATOR
# ============================================================
#
# Moved to accounts/utils.py so it can be shared with the
# interest-crediting and standing-order management commands.


# ============================================================
# TRANSACTION LIST
# ============================================================

@login_required
@permission_required(
    "accounts.view_transaction",
    raise_exception=True,
)
def transaction_list(request):

    search = request.GET.get(
        "search",
        "",
    ).strip()

    transaction_type = request.GET.get(
        "transaction_type",
        "",
    ).strip()

    status = request.GET.get(
        "status",
        "",
    ).strip()

    date_from = request.GET.get(
        "date_from",
        "",
    ).strip()

    date_to = request.GET.get(
        "date_to",
        "",
    ).strip()

    transactions = (
        Transaction.objects
        .select_related(
            "account",
            "account__customer",
            "destination_account",
            "destination_account__customer",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    transactions = scope_to_branch(
        transactions, request.user, branch_field="account__branch",
    )

    # --------------------------------------------------------
    # Search
    # --------------------------------------------------------

    if search:

        transactions = transactions.filter(
            Q(reference__icontains=search)
            | Q(
                account__account_number__icontains=search,
            )
            | Q(
                account__customer__name__icontains=search,
            )
        )

    # --------------------------------------------------------
    # Transaction type
    # --------------------------------------------------------

    if transaction_type:

        transactions = transactions.filter(
            transaction_type=transaction_type,
        )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if status:

        transactions = transactions.filter(
            status=status,
        )

    # --------------------------------------------------------
    # Date range
    # --------------------------------------------------------

    if date_from:

        transactions = transactions.filter(
            created_at__date__gte=date_from,
        )

    if date_to:

        transactions = transactions.filter(
            created_at__date__lte=date_to,
        )

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    paginator = Paginator(
        transactions,
        10,
    )

    page_number = request.GET.get(
        "page",
    )

    page_obj = paginator.get_page(
        page_number,
    )

    return render(
        request,
        "accounts/transaction_list.html",
        {
            "transactions": page_obj,
            "page_obj": page_obj,

            "search": search,
            "transaction_type": transaction_type,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


# ============================================================
# TRANSACTION DETAIL
# ============================================================

@login_required
@permission_required(
    "accounts.view_transaction",
    raise_exception=True,
)
def transaction_detail(
    request,
    transaction_id,
):

    transaction = get_object_or_404(
        Transaction.objects.select_related(
            "account",
            "account__customer",
            "destination_account",
            "destination_account__customer",
        ),
        id=transaction_id,
    )

    return render(
        request,
        "accounts/transaction_detail.html",
        {
            "transaction": transaction,
        },
    )


# ============================================================
# CREATE TRANSACTION
# ============================================================

@login_required
@permission_required(
    "accounts.add_transaction",
    raise_exception=True,
)
def transaction_create(request):

    if request.method == "POST":

        form = TransactionForm(
            request.POST,
        )

        if form.is_valid():

            account = form.cleaned_data[
                "account"
            ]

            destination_account = (
                form.cleaned_data[
                    "destination_account"
                ]
            )

            transaction_type = (
                form.cleaned_data[
                    "transaction_type"
                ]
            )

            amount = form.cleaned_data[
                "amount"
            ]

            description = form.cleaned_data[
                "description"
            ]

            try:

                with db_transaction.atomic():

                    # ------------------------------------------------
                    # Lock source account
                    # ------------------------------------------------

                    account = (
                        BankAccount.objects
                        .select_for_update()
                        .get(
                            id=account.id,
                        )
                    )

                    # ------------------------------------------------
                    # Source account must be active
                    # ------------------------------------------------

                    if account.status != "ACTIVE":

                        form.add_error(
                            "account",
                            "This account is not active.",
                        )

                        raise ValueError(
                            "Account is not active.",
                        )

                    # ==================================================
                    # DEPOSIT
                    # ==================================================

                    if transaction_type == "DEPOSIT":

                        account.balance += amount

                        account.save(
                            update_fields=[
                                "balance",
                            ],
                        )

                        deposit_txn = Transaction.objects.create(
                            account=account,
                            destination_account=None,
                            transaction_type="DEPOSIT",
                            amount=amount,
                            balance_after=account.balance,
                            reference=(
                                generate_transaction_reference(
                                    account,
                                )
                            ),
                            description=description,
                            status="COMPLETED",
                        )

                        log_action(request, "CREATE", deposit_txn)

                        ledger.post_deposit(deposit_txn, user=request.user)

                        notifications.notify_transaction(deposit_txn)

                        fraud.evaluate_transaction(deposit_txn)

                        messages.success(
                            request,
                            f"Deposited ৳{amount} to "
                            f"{account.account_number}.",
                        )

                    # ==================================================
                    # WITHDRAW
                    # ==================================================

                    elif transaction_type == "WITHDRAW":

                        fee = calculate_fee("WITHDRAW", amount)

                        if account.balance < (amount + fee):

                            form.add_error(
                                "amount",
                                "Insufficient balance."
                                + (
                                    f" (includes ৳{fee} fee)"
                                    if fee else ""
                                ),
                            )

                            raise ValueError(
                                "Insufficient balance.",
                            )

                        account.balance -= (amount + fee)

                        account.save(
                            update_fields=[
                                "balance",
                            ],
                        )

                        withdraw_txn = Transaction.objects.create(
                            account=account,
                            destination_account=None,
                            transaction_type="WITHDRAW",
                            amount=amount,
                            fee_amount=fee,
                            balance_after=account.balance,
                            reference=(
                                generate_transaction_reference(
                                    account,
                                )
                            ),
                            description=description,
                            status="COMPLETED",
                        )

                        log_action(request, "CREATE", withdraw_txn)

                        ledger.post_withdraw(withdraw_txn, user=request.user)

                        notifications.notify_transaction(withdraw_txn)

                        fraud.evaluate_transaction(withdraw_txn)

                        if fee:
                            ledger.post_fee(withdraw_txn, fee, user=request.user)

                        messages.success(
                            request,
                            f"Withdrew ৳{amount} from "
                            f"{account.account_number}."
                            + (f" Fee: ৳{fee}." if fee else ""),
                        )

                    # ==================================================
                    # TRANSFER
                    # ==================================================

                    elif transaction_type == "TRANSFER":

                        if destination_account is None:

                            form.add_error(
                                "destination_account",
                                "Destination account is required.",
                            )

                            raise ValueError(
                                "Destination account required.",
                            )

                        if account.id == destination_account.id:

                            form.add_error(
                                "destination_account",
                                (
                                    "Source and destination "
                                    "accounts cannot be the same."
                                ),
                            )

                            raise ValueError(
                                "Same account transfer.",
                            )

                        destination_account = (
                            BankAccount.objects
                            .select_for_update()
                            .get(
                                id=destination_account.id,
                            )
                        )

                        if destination_account.status != "ACTIVE":

                            form.add_error(
                                "destination_account",
                                (
                                    "Destination account "
                                    "is not active."
                                ),
                            )

                            raise ValueError(
                                "Destination account inactive.",
                            )

                        if account.balance < amount:

                            form.add_error(
                                "amount",
                                (
                                    "Insufficient balance "
                                    "for transfer."
                                ),
                            )

                            raise ValueError(
                                "Insufficient balance.",
                            )

                        fee = calculate_fee("TRANSFER", amount)

                        if account.balance < (amount + fee):

                            form.add_error(
                                "amount",
                                (
                                    "Insufficient balance for "
                                    f"transfer plus ৳{fee} fee."
                                ),
                            )

                            raise ValueError(
                                "Insufficient balance for fee.",
                            )

                        # Transfer money

                        account.balance -= (amount + fee)

                        destination_account.balance += amount

                        account.save(
                            update_fields=[
                                "balance",
                            ],
                        )

                        destination_account.save(
                            update_fields=[
                                "balance",
                            ],
                        )

                        transfer_txn = Transaction.objects.create(
                            account=account,
                            destination_account=(
                                destination_account
                            ),
                            transaction_type="TRANSFER",
                            amount=amount,
                            fee_amount=fee,
                            balance_after=account.balance,
                            reference=(
                                generate_transaction_reference(
                                    account,
                                )
                            ),
                            description=description,
                            status="COMPLETED",
                        )

                        log_action(request, "CREATE", transfer_txn)

                        ledger.post_transfer(transfer_txn, user=request.user)

                        if fee:
                            ledger.post_fee(transfer_txn, fee, user=request.user)

                        notifications.notify_transaction(transfer_txn)

                        fraud.evaluate_transaction(transfer_txn)

                        messages.success(
                            request,
                            f"Transferred ৳{amount} from "
                            f"{account.account_number} to "
                            f"{destination_account.account_number}."
                            + (f" Fee: ৳{fee}." if fee else ""),
                        )

                return redirect(
                    "transaction_list",
                )

            except ValueError:

                pass

    else:

        form = TransactionForm()

    return render(
        request,
        "accounts/transaction_form.html",
        {
            "form": form,
            "title": "New Transaction",
        },
    )


# ============================================================
# UPDATE TRANSACTION
# ============================================================

@login_required
@permission_required(
    "accounts.change_transaction",
    raise_exception=True,
)
def transaction_update(
    request,
    transaction_id,
):

    transaction = get_object_or_404(
        Transaction,
        id=transaction_id,
    )

    if request.method == "POST":

        form = TransactionForm(
            request.POST,
            instance=transaction,
        )

        if form.is_valid():

            # ------------------------------------------------
            # Financial fields are not changed.
            # Only description is updated.
            # ------------------------------------------------

            transaction.description = (
                form.cleaned_data[
                    "description"
                ]
            )

            transaction.save(
                update_fields=[
                    "description",
                ],
            )

            log_action(request, "UPDATE", transaction)

            messages.success(
                request,
                f"Transaction '{transaction.reference}' updated.",
            )

            return redirect(
                "transaction_detail",
                transaction_id=transaction.id,
            )

    else:

        form = TransactionForm(
            instance=transaction,
        )

    return render(
        request,
        "accounts/transaction_form.html",
        {
            "form": form,
            "transaction": transaction,
            "title": "Edit Transaction",
        },
    )


# ============================================================
# REVERSE TRANSACTION (non-destructive)
# ============================================================
#
# Per the roadmap: never hard-delete a financial transaction.
# This creates a compensating REVERSAL transaction and marks
# the original as REVERSED - both rows, and both journal
# entries, remain in the database permanently. Gated on
# change_transaction (not delete_transaction) since nothing
# is actually deleted - see accounts.reversal for the generic
# logic that makes this work uniformly across every
# transaction type.

@login_required
@permission_required(
    "accounts.change_transaction",
    raise_exception=True,
)
def transaction_reverse(
    request,
    transaction_id,
):

    transaction = get_object_or_404(
        Transaction,
        id=transaction_id,
    )

    if request.method == "POST":

        try:

            reversal_txn = reverse_transaction(
                transaction, user=request.user, request=request,
            )

        except ReversalError as exc:

            return render(
                request,
                "accounts/transaction_confirm_reverse.html",
                {
                    "transaction": transaction,
                    "error": str(exc),
                },
            )

        messages.success(
            request,
            f"Transaction reversed via {reversal_txn.reference}. "
            "Original record kept for audit purposes.",
        )

        return redirect(
            "transaction_detail",
            transaction_id=transaction.id,
        )

    return render(
        request,
        "accounts/transaction_confirm_reverse.html",
        {
            "transaction": transaction,
        },
    )

# ============================================================
# TRANSFER PAGE
# ============================================================

@login_required
@permission_required(
    "accounts.add_transaction",
    raise_exception=True,
)
def transaction_transfer(request):

    if request.method == "POST":

        # ----------------------------------------------------
        # Force transaction type to TRANSFER
        # ----------------------------------------------------

        post_data = request.POST.copy()

        post_data[
            "transaction_type"
        ] = "TRANSFER"

        form = TransactionForm(
            post_data,
        )

        if form.is_valid():

            account = form.cleaned_data[
                "account"
            ]

            destination_account = (
                form.cleaned_data[
                    "destination_account"
                ]
            )

            amount = form.cleaned_data[
                "amount"
            ]

            description = form.cleaned_data[
                "description"
            ]

            try:

                with db_transaction.atomic():

                    # ------------------------------------------------
                    # Source account
                    # ------------------------------------------------

                    account = (
                        BankAccount.objects
                        .select_for_update()
                        .get(
                            id=account.id,
                        )
                    )

                    # ------------------------------------------------
                    # Destination required
                    # ------------------------------------------------

                    if destination_account is None:

                        form.add_error(
                            "destination_account",
                            "Destination account is required.",
                        )

                        raise ValueError(
                            "Destination account required.",
                        )

                    # ------------------------------------------------
                    # Same account
                    # ------------------------------------------------

                    if account.id == destination_account.id:

                        form.add_error(
                            "destination_account",
                            (
                                "Source and destination "
                                "accounts cannot be the same."
                            ),
                        )

                        raise ValueError(
                            "Same account.",
                        )

                    # ------------------------------------------------
                    # Destination account
                    # ------------------------------------------------

                    destination_account = (
                        BankAccount.objects
                        .select_for_update()
                        .get(
                            id=destination_account.id,
                        )
                    )

                    # ------------------------------------------------
                    # Source status
                    # ------------------------------------------------

                    if account.status != "ACTIVE":

                        form.add_error(
                            "account",
                            "Source account is not active.",
                        )

                        raise ValueError(
                            "Source account inactive.",
                        )

                    # ------------------------------------------------
                    # Destination status
                    # ------------------------------------------------

                    if destination_account.status != "ACTIVE":

                        form.add_error(
                            "destination_account",
                            (
                                "Destination account "
                                "is not active."
                            ),
                        )

                        raise ValueError(
                            "Destination account inactive.",
                        )

                    # ------------------------------------------------
                    # Balance
                    # ------------------------------------------------

                    if account.balance < amount:

                        form.add_error(
                            "amount",
                            "Insufficient balance.",
                        )

                        raise ValueError(
                            "Insufficient balance.",
                        )

                    fee = calculate_fee("TRANSFER", amount)

                    if account.balance < (amount + fee):

                        form.add_error(
                            "amount",
                            f"Insufficient balance for transfer plus ৳{fee} fee.",
                        )

                        raise ValueError(
                            "Insufficient balance for fee.",
                        )

                    # ------------------------------------------------
                    # Transfer
                    # ------------------------------------------------

                    account.balance -= (amount + fee)

                    destination_account.balance += amount

                    account.save(
                        update_fields=[
                            "balance",
                        ],
                    )

                    destination_account.save(
                        update_fields=[
                            "balance",
                        ],
                    )

                    # ------------------------------------------------
                    # Create transaction
                    # ------------------------------------------------

                    transfer_txn = Transaction.objects.create(
                        account=account,
                        destination_account=(
                            destination_account
                        ),
                        transaction_type="TRANSFER",
                        amount=amount,
                        fee_amount=fee,
                        balance_after=account.balance,
                        reference=(
                            generate_transaction_reference(
                                account,
                            )
                        ),
                        description=description,
                        status="COMPLETED",
                    )

                    log_action(request, "CREATE", transfer_txn)

                    ledger.post_transfer(transfer_txn, user=request.user)

                    if fee:
                        ledger.post_fee(transfer_txn, fee, user=request.user)

                    notifications.notify_transaction(transfer_txn)

                    fraud.evaluate_transaction(transfer_txn)

                    messages.success(
                        request,
                        f"Transferred ৳{amount} from "
                        f"{account.account_number} to "
                        f"{destination_account.account_number}."
                        + (f" Fee: ৳{fee}." if fee else ""),
                    )

                return redirect(
                    "transaction_list",
                )

            except ValueError:

                pass

    else:

        form = TransactionForm(
            initial={
                "transaction_type": "TRANSFER",
            }
        )

    return render(
        request,
        "accounts/transaction_form.html",
        {
            "form": form,
            "title": "Create Transfer",
            "transfer_mode": True,
        },
    )

# ============================================================
# STANDING ORDERS
# ============================================================

@login_required
@permission_required(
    "accounts.view_standingorder",
    raise_exception=True,
)
def standing_order_list(request):

    orders = (
        StandingOrder.objects
        .select_related(
            "account",
            "account__customer",
            "destination_account",
        )
        .order_by("-is_active", "next_run_date")
    )

    return render(
        request,
        "accounts/standing_order_list.html",
        {
            "orders": orders,
        },
    )


@login_required
@permission_required(
    "accounts.add_standingorder",
    raise_exception=True,
)
def standing_order_create(request):

    if request.method == "POST":

        form = StandingOrderForm(
            request.POST,
        )

        if form.is_valid():

            order = form.save()

            log_action(request, "CREATE", order)

            messages.success(
                request,
                f"Standing order created: ৳{order.amount} "
                f"{order.get_frequency_display().lower()} "
                f"{order.account.account_number} -> "
                f"{order.destination_account.account_number}.",
            )

            return redirect(
                "standing_order_list",
            )

    else:

        form = StandingOrderForm()

    return render(
        request,
        "accounts/standing_order_form.html",
        {
            "form": form,
            "title": "New Standing Order",
        },
    )


@login_required
@permission_required(
    "accounts.change_standingorder",
    raise_exception=True,
)
def standing_order_update(
    request,
    order_id,
):

    order = get_object_or_404(
        StandingOrder,
        id=order_id,
    )

    if request.method == "POST":

        form = StandingOrderForm(
            request.POST,
            instance=order,
        )

        if form.is_valid():

            order = form.save()

            log_action(request, "UPDATE", order)

            messages.success(
                request,
                "Standing order updated.",
            )

            return redirect(
                "standing_order_list",
            )

    else:

        form = StandingOrderForm(
            instance=order,
        )

    return render(
        request,
        "accounts/standing_order_form.html",
        {
            "form": form,
            "order": order,
            "title": "Edit Standing Order",
        },
    )


@login_required
@permission_required(
    "accounts.delete_standingorder",
    raise_exception=True,
)
def standing_order_delete(
    request,
    order_id,
):

    order = get_object_or_404(
        StandingOrder,
        id=order_id,
    )

    if request.method == "POST":

        order_id_str = str(order.id)

        order.delete()

        log_action(
            request,
            "DELETE",
            order,
            object_id=order_id_str,
        )

        messages.success(
            request,
            "Standing order deleted.",
        )

        return redirect(
            "standing_order_list",
        )

    return render(
        request,
        "accounts/standing_order_confirm_delete.html",
        {
            "order": order,
        },
    )


@login_required
@permission_required(
    "accounts.change_standingorder",
    raise_exception=True,
)
def standing_order_toggle(
    request,
    order_id,
):

    """
    Quick pause/resume toggle from the list page. Only
    accepts POST to avoid a GET request accidentally
    flipping the state.
    """

    order = get_object_or_404(
        StandingOrder,
        id=order_id,
    )

    if request.method == "POST":

        order.is_active = not order.is_active

        order.save(
            update_fields=["is_active"],
        )

        log_action(
            request,
            "UPDATE",
            order,
            note=(
                "Activated" if order.is_active else "Paused"
            ),
        )

        messages.success(
            request,
            "Standing order resumed." if order.is_active
            else "Standing order paused.",
        )

    return redirect(
        "standing_order_list",
    )


# ============================================================
# AUDIT LOG
# ============================================================

@login_required
@permission_required(
    "accounts.view_auditlog",
    raise_exception=True,
)
def audit_log_list(request):

    search = request.GET.get("search", "").strip()
    action = request.GET.get("action", "").strip()
    model_name = request.GET.get("model_name", "").strip()

    logs = AuditLog.objects.select_related("user").all()

    if search:

        logs = logs.filter(
            Q(object_repr__icontains=search)
            | Q(object_id__icontains=search)
            | Q(user__username__icontains=search)
        )

    if action:

        logs = logs.filter(action=action)

    if model_name:

        logs = logs.filter(model_name=model_name)

    paginator = Paginator(logs, 25)

    page_number = request.GET.get("page")

    page_obj = paginator.get_page(page_number)

    model_choices = (
        AuditLog.objects
        .order_by()
        .values_list("model_name", flat=True)
        .distinct()
    )

    return render(
        request,
        "accounts/audit_log_list.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "search": search,
            "action": action,
            "model_name": model_name,
            "model_choices": model_choices,
        },
    )


# ============================================================
# LOANS
# ============================================================

@login_required
@permission_required(
    "accounts.view_loan",
    raise_exception=True,
)
def loan_list(request):

    loans = (
        Loan.objects
        .select_related("account", "account__customer")
        .order_by("-created_at")
    )

    loans = scope_to_branch(
        loans, request.user, branch_field="account__branch",
    )

    return render(
        request,
        "accounts/loan_list.html",
        {
            "loans": loans,
        },
    )


@login_required
@permission_required(
    "accounts.add_loan",
    raise_exception=True,
)
def loan_create(request):

    if request.method == "POST":

        form = LoanForm(
            request.POST,
        )

        if form.is_valid():

            with db_transaction.atomic():

                loan = form.save(commit=False)

                account = (
                    BankAccount.objects
                    .select_for_update()
                    .get(id=loan.account_id)
                )

                if account.status != "ACTIVE":

                    form.add_error(
                        None,
                        f"Account {account.account_number} is not "
                        "active - cannot disburse a loan to it.",
                    )

                else:

                    loan.save()

                    account.balance += loan.principal
                    account.save(update_fields=["balance"])

                    disbursement_txn = Transaction.objects.create(
                        account=account,
                        destination_account=None,
                        transaction_type="LOAN_DISBURSEMENT",
                        amount=loan.principal,
                        balance_after=account.balance,
                        reference=generate_transaction_reference(account),
                        description=f"Loan #{loan.id} disbursement",
                        status="COMPLETED",
                    )

                    log_action(request, "CREATE", loan)
                    log_action(request, "CREATE", disbursement_txn)

                    ledger.post_loan_disbursement(
                        loan, transaction=disbursement_txn, user=request.user,
                    )

                    notifications.notify_transaction(disbursement_txn)

                    schedule = amortization_schedule(
                        loan.principal, loan.annual_rate,
                        loan.months, loan.start_date,
                    )

                    LoanInstallment.objects.bulk_create([
                        LoanInstallment(
                            loan=loan,
                            installment_no=row["month_no"],
                            due_date=row["date"],
                            principal_due=row["principal"],
                            interest_due=row["interest"],
                            total_due=row["emi"],
                        )
                        for row in schedule
                    ])

                    messages.success(
                        request,
                        f"Loan of ৳{loan.principal} disbursed to "
                        f"{account.account_number}. New balance: "
                        f"৳{account.balance}.",
                    )

                    return redirect(
                        "loan_detail",
                        loan_id=loan.id,
                    )

    else:

        form = LoanForm()

    return render(
        request,
        "accounts/loan_form.html",
        {
            "form": form,
            "title": "New Loan",
        },
    )


@login_required
@permission_required(
    "accounts.view_loan",
    raise_exception=True,
)
def loan_detail(
    request,
    loan_id,
):

    loan = get_object_or_404(
        Loan.objects.select_related("account", "account__customer"),
        id=loan_id,
    )

    emi = calculate_emi(
        loan.principal,
        loan.annual_rate,
        loan.months,
    )

    schedule = amortization_schedule(
        loan.principal,
        loan.annual_rate,
        loan.months,
        loan.start_date,
    )

    total_payable = emi * loan.months
    total_interest = total_payable - loan.principal

    installments = loan.installments.all()

    next_installment = installments.filter(
        status__in=["PENDING", "OVERDUE"],
    ).order_by("installment_no").first()

    next_installment_total = None

    if next_installment is not None:
        next_installment_total = (
            next_installment.total_due + next_installment.penalty_amount
        )

    total_paid = sum(
        (i.amount_paid for i in installments), Decimal("0.00"),
    )

    return render(
        request,
        "accounts/loan_detail.html",
        {
            "loan": loan,
            "emi": emi,
            "schedule": schedule,
            "installments": installments,
            "next_installment": next_installment,
            "next_installment_total": next_installment_total,
            "total_paid": total_paid,
            "total_payable": total_payable,
            "total_interest": total_interest,
        },
    )


@login_required
@permission_required(
    "accounts.change_loan",
    raise_exception=True,
)
def loan_repay(
    request,
    loan_id,
):

    loan = get_object_or_404(
        Loan.objects.select_related("account"),
        id=loan_id,
    )

    next_installment = (
        loan.installments
        .filter(status__in=["PENDING", "OVERDUE"])
        .order_by("installment_no")
        .first()
    )

    if next_installment is None:

        messages.warning(
            request,
            "This loan has no outstanding installments.",
        )

        return redirect("loan_detail", loan_id=loan.id)

    total_due = next_installment.total_due + next_installment.penalty_amount

    if request.method == "POST":

        with db_transaction.atomic():

            account = (
                BankAccount.objects
                .select_for_update()
                .get(id=loan.account_id)
            )

            if account.balance < total_due:

                return render(
                    request,
                    "accounts/loan_repay_confirm.html",
                    {
                        "loan": loan,
                        "installment": next_installment,
                        "total_due": total_due,
                        "error": (
                            f"Insufficient balance on "
                            f"{account.account_number} to pay "
                            f"this installment."
                        ),
                    },
                )

            account.balance -= total_due
            account.save(update_fields=["balance"])

            repayment_txn = Transaction.objects.create(
                account=account,
                destination_account=None,
                transaction_type="LOAN_REPAYMENT",
                amount=total_due,
                balance_after=account.balance,
                reference=generate_transaction_reference(account),
                description=(
                    f"Loan #{loan.id} installment "
                    f"{next_installment.installment_no} repayment"
                ),
                status="COMPLETED",
            )

            log_action(request, "CREATE", repayment_txn)

            ledger.post_loan_repayment(
                repayment_txn,
                principal_portion=next_installment.principal_due,
                interest_portion=next_installment.interest_due,
                penalty_portion=next_installment.penalty_amount,
                user=request.user,
            )

            notifications.notify_transaction(repayment_txn)

            next_installment.amount_paid = total_due
            next_installment.paid_date = timezone.localdate()
            next_installment.status = "PAID"
            next_installment.save(
                update_fields=["amount_paid", "paid_date", "status"],
            )

            log_action(request, "UPDATE", next_installment)

            remaining = loan.installments.exclude(status="PAID").exists()

            if not remaining:

                loan.status = "CLOSED"
                loan.save(update_fields=["status"])

                log_action(
                    request, "UPDATE", loan, note="Auto-closed: fully repaid",
                )

            messages.success(
                request,
                f"Installment {next_installment.installment_no} "
                f"(৳{total_due}) paid."
                + (" Loan fully repaid and closed." if not remaining else ""),
            )

            return redirect("loan_detail", loan_id=loan.id)

    return render(
        request,
        "accounts/loan_repay_confirm.html",
        {
            "loan": loan,
            "installment": next_installment,
            "total_due": total_due,
        },
    )


@login_required
@permission_required(
    "accounts.change_loan",
    raise_exception=True,
)
def loan_close(
    request,
    loan_id,
):

    loan = get_object_or_404(
        Loan,
        id=loan_id,
    )

    if request.method == "POST":

        loan.status = "CLOSED"

        loan.save(
            update_fields=["status"],
        )

        log_action(
            request,
            "UPDATE",
            loan,
            note="Marked closed",
        )

        messages.success(
            request,
            "Loan marked as closed.",
        )

    return redirect(
        "loan_detail",
        loan_id=loan.id,
    )


# ============================================================
# EMI CALCULATOR (standalone, no loan record required)
# ============================================================

@login_required
@permission_required(
    "accounts.view_loan",
    raise_exception=True,
)
def emi_calculator(request):

    """
    A quick what-if calculator - ports calc_emi directly
    without requiring a saved Loan record. Useful for staff
    quoting a rate to a customer before anything is committed.
    """

    result = None

    if request.method == "POST":

        try:

            principal = Decimal(request.POST.get("principal", "0"))
            annual_rate = Decimal(request.POST.get("annual_rate", "0"))
            months = int(request.POST.get("months", "0"))

            if principal > 0 and months > 0:

                emi = calculate_emi(principal, annual_rate, months)
                total_payable = emi * months
                total_interest = total_payable - principal

                result = {
                    "principal": principal,
                    "annual_rate": annual_rate,
                    "months": months,
                    "emi": emi,
                    "total_payable": total_payable,
                    "total_interest": total_interest,
                }

        except (ValueError, ArithmeticError, TypeError):

            result = {"error": "Please enter valid numbers."}

    return render(
        request,
        "accounts/emi_calculator.html",
        {
            "result": result,
        },
    )


# ============================================================
# CSV EXPORTS
# ============================================================

@login_required
@permission_required(
    "accounts.view_transaction",
    raise_exception=True,
)
def transaction_export_csv(request):

    """
    Exports the transaction list as CSV, honoring the exact
    same search/type/status/date-range filters as the
    transaction_list page (whatever's in the query string
    when you click "Export CSV" is what you get).
    """

    transactions = (
        Transaction.objects
        .select_related(
            "account",
            "account__customer",
            "destination_account",
        )
        .order_by("-created_at", "-id")
    )

    transactions = filter_transactions(request, transactions)

    response = HttpResponse(content_type="text/csv")

    response["Content-Disposition"] = (
        'attachment; filename="transactions.csv"'
    )

    writer = csv.writer(response)

    writer.writerow([
        "Reference",
        "Account",
        "Customer",
        "Type",
        "Amount",
        "Destination Account",
        "Status",
        "Description",
        "Date",
    ])

    for txn in transactions:

        writer.writerow([
            txn.reference,
            txn.account.account_number,
            txn.account.customer.name,
            txn.get_transaction_type_display(),
            txn.amount,
            (
                txn.destination_account.account_number
                if txn.destination_account
                else ""
            ),
            txn.get_status_display(),
            txn.description,
            txn.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    return response


@login_required
@permission_required(
    "accounts.view_bankaccount",
    raise_exception=True,
)
def bank_account_statement_csv(
    request,
    account_id,
):

    """
    Exports a single account's transaction history as CSV -
    the "statement" for that account.
    """

    account = get_object_or_404(
        BankAccount,
        id=account_id,
    )

    transactions = (
        Transaction.objects
        .filter(account=account)
        .order_by("-created_at", "-id")
    )

    response = HttpResponse(content_type="text/csv")

    response["Content-Disposition"] = (
        f'attachment; filename="statement_{account.account_number}.csv"'
    )

    writer = csv.writer(response)

    writer.writerow([f"Account Statement - {account.account_number}"])
    writer.writerow([f"Customer: {account.customer.name}"])
    writer.writerow([f"Current Balance: {account.balance}"])
    writer.writerow([])

    writer.writerow([
        "Reference",
        "Type",
        "Amount",
        "Balance After",
        "Status",
        "Description",
        "Date",
    ])

    for txn in transactions:

        writer.writerow([
            txn.reference,
            txn.get_transaction_type_display(),
            txn.amount,
            txn.balance_after,
            txn.get_status_display(),
            txn.description,
            txn.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    return response


# ============================================================
# BRANCHES
# ============================================================

@login_required
@permission_required(
    "accounts.view_branch",
    raise_exception=True,
)
def branch_list(request):

    branches = (
        Branch.objects
        .annotate(
            customers_total=Count("customers", distinct=True),
            accounts_total=Count("bank_accounts", distinct=True),
            employees_total=Count("employees", distinct=True),
        )
        .order_by("code")
    )

    return render(
        request,
        "accounts/branch_list.html",
        {
            "branches": branches,
        },
    )


@login_required
@permission_required(
    "accounts.add_branch",
    raise_exception=True,
)
def branch_create(request):

    if request.method == "POST":

        form = BranchForm(request.POST)

        if form.is_valid():

            branch = form.save()

            log_action(request, "CREATE", branch)

            messages.success(
                request,
                f"Branch '{branch}' created.",
            )

            return redirect("branch_list")

    else:

        form = BranchForm()

    return render(
        request,
        "accounts/branch_form.html",
        {
            "form": form,
            "title": "Add Branch",
        },
    )


@login_required
@permission_required(
    "accounts.change_branch",
    raise_exception=True,
)
def branch_update(request, branch_id):

    branch = get_object_or_404(Branch, id=branch_id)

    if request.method == "POST":

        form = BranchForm(request.POST, instance=branch)

        if form.is_valid():

            branch = form.save()

            log_action(request, "UPDATE", branch)

            messages.success(
                request,
                f"Branch '{branch}' updated.",
            )

            return redirect("branch_list")

    else:

        form = BranchForm(instance=branch)

    return render(
        request,
        "accounts/branch_form.html",
        {
            "form": form,
            "branch": branch,
            "title": "Edit Branch",
        },
    )


@login_required
@permission_required(
    "accounts.delete_branch",
    raise_exception=True,
)
def branch_delete(request, branch_id):

    branch = get_object_or_404(Branch, id=branch_id)

    if request.method == "POST":

        try:

            branch_id_str = str(branch.id)
            branch_repr = str(branch)

            branch.delete()

            log_action(
                request,
                "DELETE",
                branch,
                object_id=branch_id_str,
            )

            messages.success(
                request,
                f"Branch '{branch_repr}' deleted.",
            )

        except ProtectedError:

            return render(
                request,
                "accounts/branch_confirm_delete.html",
                {
                    "branch": branch,
                    "error": (
                        "This branch cannot be deleted because it "
                        "still has customers, accounts, or employees "
                        "assigned to it. Reassign them first."
                    ),
                },
            )

        return redirect("branch_list")

    return render(
        request,
        "accounts/branch_confirm_delete.html",
        {
            "branch": branch,
        },
    )


# ============================================================
# EMPLOYEE ROLE ASSIGNMENT
# ============================================================

@login_required
@permission_required(
    "accounts.view_employeeprofile",
    raise_exception=True,
)
def employee_list(request):

    employees = (
        EmployeeProfile.objects
        .select_related("user", "branch")
        .order_by("branch__code", "user__username")
    )

    unassigned_users = (
        User.objects
        .filter(employee_profile__isnull=True)
        .order_by("username")
    )

    return render(
        request,
        "accounts/employee_list.html",
        {
            "employees": employees,
            "unassigned_users": unassigned_users,
        },
    )


@login_required
@permission_required(
    "accounts.add_employeeprofile",
    raise_exception=True,
)
def employee_assign(request):

    if request.method == "POST":

        form = EmployeeProfileForm(request.POST)

        if form.is_valid():

            profile = form.save()

            log_action(request, "CREATE", profile)

            messages.success(
                request,
                f"{profile.user.username} assigned as "
                f"{profile.get_role_display()}.",
            )

            return redirect("employee_list")

    else:

        form = EmployeeProfileForm()

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "title": "Assign Employee Role",
        },
    )


@login_required
@permission_required(
    "accounts.change_employeeprofile",
    raise_exception=True,
)
def employee_update(request, profile_id):

    profile = get_object_or_404(EmployeeProfile, id=profile_id)

    if request.method == "POST":

        form = EmployeeProfileForm(request.POST, instance=profile)

        if form.is_valid():

            profile = form.save()

            log_action(request, "UPDATE", profile)

            messages.success(
                request,
                f"Updated {profile.user.username}'s role to "
                f"{profile.get_role_display()}.",
            )

            return redirect("employee_list")

    else:

        form = EmployeeProfileForm(instance=profile)

        # The user shouldn't be reassigned to a different
        # profile after creation - only role/branch/employee_id
        # change here.
        form.fields["user"].disabled = True

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "profile": profile,
            "title": f"Edit Role - {profile.user.username}",
        },
    )


# ============================================================
# LEDGER (TRIAL BALANCE / CHART OF ACCOUNTS)
# ============================================================

@login_required
@permission_required(
    "accounts.view_chartofaccount",
    raise_exception=True,
)
def trial_balance(request):

    """
    The proof that the double-entry books actually balance:
    for every ChartOfAccount, sums its debit and credit
    journal lines, computes a signed balance according to its
    normal_balance side, and totals debits vs credits across
    the whole system - which must always be equal. If they
    ever aren't, something bypassed accounts.ledger.post_journal_entry
    and wrote unbalanced entries directly.
    """

    accounts_qs = ChartOfAccount.objects.all().order_by("code")

    rows = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for coa in accounts_qs:

        totals = coa.journal_lines.aggregate(
            debit_sum=Sum("debit"),
            credit_sum=Sum("credit"),
        )

        debit_sum = totals["debit_sum"] or Decimal("0.00")
        credit_sum = totals["credit_sum"] or Decimal("0.00")

        if coa.normal_balance == "DEBIT":
            balance = debit_sum - credit_sum
        else:
            balance = credit_sum - debit_sum

        total_debit += debit_sum
        total_credit += credit_sum

        rows.append({
            "account": coa,
            "debit_sum": debit_sum,
            "credit_sum": credit_sum,
            "balance": balance,
        })

    return render(
        request,
        "accounts/trial_balance.html",
        {
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "is_balanced": total_debit == total_credit,
        },
    )


@login_required
@permission_required(
    "accounts.view_chartofaccount",
    raise_exception=True,
)
def ledger_account_detail(request, coa_id):

    coa = get_object_or_404(ChartOfAccount, id=coa_id)

    lines = (
        coa.journal_lines
        .select_related("journal_entry", "bank_account")
        .order_by("-journal_entry__created_at", "-id")
    )

    running_balance = Decimal("0.00")
    rows = []

    # Compute running balance oldest-first, then reverse for
    # newest-first display.
    for line in reversed(list(lines)):

        if coa.normal_balance == "DEBIT":
            running_balance += line.debit - line.credit
        else:
            running_balance += line.credit - line.debit

        rows.append({
            "line": line,
            "running_balance": running_balance,
        })

    rows.reverse()

    return render(
        request,
        "accounts/ledger_account_detail.html",
        {
            "coa": coa,
            "rows": rows,
        },
    )


# ============================================================
# FEE RULES
# ============================================================

@login_required
@permission_required(
    "accounts.view_feerule",
    raise_exception=True,
)
def fee_rule_list(request):

    fee_rules = FeeRule.objects.all().order_by("transaction_type", "name")

    return render(
        request,
        "accounts/fee_rule_list.html",
        {
            "fee_rules": fee_rules,
        },
    )


@login_required
@permission_required(
    "accounts.add_feerule",
    raise_exception=True,
)
def fee_rule_create(request):

    if request.method == "POST":

        form = FeeRuleForm(request.POST)

        if form.is_valid():

            fee_rule = form.save()

            log_action(request, "CREATE", fee_rule)

            messages.success(
                request,
                f"Fee rule '{fee_rule.name}' created "
                f"({'active' if fee_rule.is_active else 'inactive'}).",
            )

            return redirect("fee_rule_list")

    else:

        form = FeeRuleForm()

    return render(
        request,
        "accounts/fee_rule_form.html",
        {
            "form": form,
            "title": "New Fee Rule",
        },
    )


@login_required
@permission_required(
    "accounts.change_feerule",
    raise_exception=True,
)
def fee_rule_update(request, fee_rule_id):

    fee_rule = get_object_or_404(FeeRule, id=fee_rule_id)

    if request.method == "POST":

        form = FeeRuleForm(request.POST, instance=fee_rule)

        if form.is_valid():

            fee_rule = form.save()

            log_action(request, "UPDATE", fee_rule)

            messages.success(
                request,
                f"Fee rule '{fee_rule.name}' updated.",
            )

            return redirect("fee_rule_list")

    else:

        form = FeeRuleForm(instance=fee_rule)

    return render(
        request,
        "accounts/fee_rule_form.html",
        {
            "form": form,
            "fee_rule": fee_rule,
            "title": f"Edit Fee Rule - {fee_rule.name}",
        },
    )


# ============================================================
# CUSTOMER PORTAL ACCESS (staff-managed)
# ============================================================

@login_required
@permission_required(
    "accounts.change_customer",
    raise_exception=True,
)
def customer_portal_enable(request, customer_id):

    """
    Creates a Django User + CustomerPortalAccount for a
    customer who doesn't have one yet, with a random generated
    password shown ONCE on the confirmation page - staff must
    communicate it to the customer through a separate secure
    channel (never emailed in plaintext).
    """

    import secrets

    customer = get_object_or_404(Customer, id=customer_id)

    if hasattr(customer, "portal_account"):

        messages.warning(
            request,
            f"{customer.name} already has portal access.",
        )

        return redirect("customer_detail", customer_id=customer.id)

    if request.method == "POST":

        username = request.POST.get("username", "").strip()

        if not username:

            messages.error(request, "Username is required.")

            return render(
                request,
                "accounts/customer_portal_enable.html",
                {"customer": customer},
            )

        if User.objects.filter(username=username).exists():

            messages.error(
                request,
                f"Username '{username}' is already taken.",
            )

            return render(
                request,
                "accounts/customer_portal_enable.html",
                {"customer": customer},
            )

        password = secrets.token_urlsafe(9)

        user = User.objects.create_user(
            username=username,
            password=password,
            email=customer.email,
            is_staff=False,
        )

        portal_account = CustomerPortalAccount.objects.create(
            user=user, customer=customer,
        )

        log_action(request, "CREATE", portal_account)

        notifications.notify_portal_account_created(customer, username)

        return render(
            request,
            "accounts/customer_portal_created.html",
            {
                "customer": customer,
                "username": username,
                "password": password,
            },
        )

    return render(
        request,
        "accounts/customer_portal_enable.html",
        {
            "customer": customer,
        },
    )


@login_required
@permission_required(
    "accounts.change_customer",
    raise_exception=True,
)
def customer_portal_reset_password(request, customer_id):

    import secrets

    customer = get_object_or_404(Customer, id=customer_id)

    portal_account = get_object_or_404(
        CustomerPortalAccount, customer=customer,
    )

    if request.method == "POST":

        password = secrets.token_urlsafe(9)

        portal_account.user.set_password(password)
        portal_account.user.save()

        log_action(
            request, "UPDATE", portal_account, note="Password reset",
        )

        return render(
            request,
            "accounts/customer_portal_created.html",
            {
                "customer": customer,
                "username": portal_account.user.username,
                "password": password,
                "is_reset": True,
            },
        )

    return render(
        request,
        "accounts/customer_portal_reset_confirm.html",
        {
            "customer": customer,
        },
    )


# ============================================================
# FRAUD ALERTS
# ============================================================

@login_required
@permission_required(
    "accounts.view_fraudalert",
    raise_exception=True,
)
def fraud_alert_list(request):

    status = request.GET.get("status", "").strip()

    alerts = (
        FraudAlert.objects
        .select_related(
            "transaction",
            "transaction__account",
            "transaction__account__customer",
        )
        .all()
    )

    alerts = scope_to_branch(
        alerts, request.user, branch_field="transaction__account__branch",
    )

    if status:
        alerts = alerts.filter(status=status)

    return render(
        request,
        "accounts/fraud_alert_list.html",
        {
            "alerts": alerts,
            "status": status,
        },
    )


@login_required
@permission_required(
    "accounts.view_fraudalert",
    raise_exception=True,
)
def fraud_alert_detail(request, alert_id):

    alert = get_object_or_404(
        FraudAlert.objects.select_related(
            "transaction",
            "transaction__account",
            "transaction__account__customer",
        ),
        id=alert_id,
    )

    return render(
        request,
        "accounts/fraud_alert_detail.html",
        {
            "alert": alert,
        },
    )


@login_required
@permission_required(
    "accounts.change_fraudalert",
    raise_exception=True,
)
def fraud_alert_resolve(request, alert_id):

    alert = get_object_or_404(FraudAlert, id=alert_id)

    if request.method == "POST":

        new_status = request.POST.get("status")

        if new_status not in ("CONFIRMED_FRAUD", "FALSE_POSITIVE"):

            messages.error(request, "Invalid resolution status.")

            return redirect("fraud_alert_detail", alert_id=alert.id)

        alert.status = new_status
        alert.reviewed_by = request.user
        alert.reviewed_at = timezone.now()

        alert.save(
            update_fields=["status", "reviewed_by", "reviewed_at"],
        )

        log_action(
            request, "UPDATE", alert,
            note=f"Resolved as {alert.get_status_display()}",
        )

        if new_status == "CONFIRMED_FRAUD":

            account = alert.transaction.account

            account.status = "BLOCKED"
            account.save(update_fields=["status"])

            log_action(
                request, "UPDATE", account,
                note=f"Blocked due to confirmed fraud alert #{alert.id}",
            )

            messages.warning(
                request,
                f"Alert confirmed as fraud. Account "
                f"{account.account_number} has been blocked.",
            )

        else:

            messages.success(request, "Alert marked as false positive.")

        return redirect("fraud_alert_list")

    return redirect("fraud_alert_detail", alert_id=alert.id)
