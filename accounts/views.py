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

from .utils import (
    generate_transaction_reference,
    log_action,
    calculate_emi,
    amortization_schedule,
    filter_transactions,
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
)

from .models import (
    Customer,
    BankAccount,
    Transaction,
    StandingOrder,
    AuditLog,
    Loan,
    DailySnapshot,
    Branch,
    EmployeeProfile,
)


# ============================================================
# HOME / DASHBOARD
# ============================================================

@login_required
def home(request):

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

                        messages.success(
                            request,
                            f"Deposited ৳{amount} to "
                            f"{account.account_number}.",
                        )

                    # ==================================================
                    # WITHDRAW
                    # ==================================================

                    elif transaction_type == "WITHDRAW":

                        if account.balance < amount:

                            form.add_error(
                                "amount",
                                "Insufficient balance.",
                            )

                            raise ValueError(
                                "Insufficient balance.",
                            )

                        account.balance -= amount

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

                        messages.success(
                            request,
                            f"Withdrew ৳{amount} from "
                            f"{account.account_number}.",
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

                        # Transfer money

                        account.balance -= amount

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

                        messages.success(
                            request,
                            f"Transferred ৳{amount} from "
                            f"{account.account_number} to "
                            f"{destination_account.account_number}.",
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
# DELETE TRANSACTION
# ============================================================

@login_required
@permission_required(
    "accounts.delete_transaction",
    raise_exception=True,
)
def transaction_delete(
    request,
    transaction_id,
):

    transaction = get_object_or_404(
        Transaction,
        id=transaction_id,
    )

    if request.method == "POST":

        # ----------------------------------------------------
        # Only latest transaction can be deleted.
        # ----------------------------------------------------

        latest_transaction = (
            Transaction.objects
            .filter(
                account=transaction.account,
            )
            .order_by(
                "-created_at",
                "-id",
            )
            .first()
        )

        if latest_transaction != transaction:

            return render(
                request,
                "accounts/transaction_confirm_delete.html",
                {
                    "transaction": transaction,
                    "error": (
                        "Only the most recent transaction "
                        "can be deleted."
                    ),
                },
            )

        with db_transaction.atomic():

            transaction_id_str = str(transaction.id)

            account = (
                BankAccount.objects
                .select_for_update()
                .get(
                    id=transaction.account.id,
                )
            )

            # ==================================================
            # REVERSE DEPOSIT
            # ==================================================

            if transaction.transaction_type == "DEPOSIT":

                account.balance -= transaction.amount

                account.save(
                    update_fields=[
                        "balance",
                    ],
                )

            # ==================================================
            # REVERSE WITHDRAW
            # ==================================================

            elif transaction.transaction_type == "WITHDRAW":

                account.balance += transaction.amount

                account.save(
                    update_fields=[
                        "balance",
                    ],
                )

            # ==================================================
            # REVERSE TRANSFER
            # ==================================================

            elif transaction.transaction_type == "TRANSFER":

                destination = (
                    transaction.destination_account
                )

                if destination is not None:

                    destination = (
                        BankAccount.objects
                        .select_for_update()
                        .get(
                            id=destination.id,
                        )
                    )

                    destination.balance -= (
                        transaction.amount
                    )

                    destination.save(
                        update_fields=[
                            "balance",
                        ],
                    )

                account.balance += (
                    transaction.amount
                )

                account.save(
                    update_fields=[
                        "balance",
                    ],
                )

            transaction.delete()

            log_action(
                request,
                "DELETE",
                transaction,
                object_id=transaction_id_str,
            )

            messages.success(
                request,
                "Transaction deleted and balance reversed.",
            )

        return redirect(
            "transaction_list",
        )

    return render(
        request,
        "accounts/transaction_confirm_delete.html",
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

                    # ------------------------------------------------
                    # Transfer
                    # ------------------------------------------------

                    account.balance -= amount

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

                    messages.success(
                        request,
                        f"Transferred ৳{amount} from "
                        f"{account.account_number} to "
                        f"{destination_account.account_number}.",
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

            loan = form.save()

            log_action(request, "CREATE", loan)

            messages.success(
                request,
                f"Loan of ৳{loan.principal} created for "
                f"{loan.account.account_number}.",
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

    return render(
        request,
        "accounts/loan_detail.html",
        {
            "loan": loan,
            "emi": emi,
            "schedule": schedule,
            "total_payable": total_payable,
            "total_interest": total_interest,
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
