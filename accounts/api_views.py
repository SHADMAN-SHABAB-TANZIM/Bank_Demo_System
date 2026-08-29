from rest_framework import viewsets
from rest_framework.permissions import DjangoModelPermissions

from .branch_scope import scope_to_branch
from .models import Customer, BankAccount, Transaction, StandingOrder, Loan
from .serializers import (
    CustomerSerializer,
    BankAccountSerializer,
    TransactionSerializer,
    StandingOrderSerializer,
    LoanSerializer,
)
from .utils import log_action


class StrictDjangoModelPermissions(DjangoModelPermissions):

    """
    DRF's DjangoModelPermissions does not require the
    `view_<model>` permission for GET/HEAD/OPTIONS by default.
    Every other view in this project is permission-gated
    (including read-only ones, via
    @permission_required("accounts.view_...")), so this
    extends it to require `view_<model>` for safe methods too.
    """

    def __init__(self):

        self.perms_map = dict(self.perms_map)
        self.perms_map["GET"] = ["%(app_label)s.view_%(model_name)s"]
        self.perms_map["HEAD"] = ["%(app_label)s.view_%(model_name)s"]
        self.perms_map["OPTIONS"] = ["%(app_label)s.view_%(model_name)s"]


class CustomerViewSet(viewsets.ModelViewSet):

    """
    /api/v1/customers/        GET, POST
    /api/v1/customers/{id}/   GET, PUT, PATCH, DELETE

    Uses the same accounts.* Django permissions as the
    staff UI (add_customer, change_customer, delete_customer,
    view_customer), and the same branch scoping - a Branch
    Manager/Teller/etc. sees only their own branch's customers
    through the API too, matching the staff UI exactly.

    Filtering: ?branch=<id>&is_active=true
    Search: ?search=<name or email>
    Ordering: ?ordering=name or ?ordering=-created_at
    """

    serializer_class = CustomerSerializer
    permission_classes = [StrictDjangoModelPermissions]

    filterset_fields = ["branch", "is_active"]
    search_fields = ["name", "email"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):

        queryset = Customer.objects.all().order_by("name")

        return scope_to_branch(queryset, self.request.user)

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)

    def perform_update(self, serializer):

        instance = serializer.save()

        log_action(self.request, "UPDATE", instance)

    def perform_destroy(self, instance):

        object_id = str(instance.id)

        instance.delete()

        log_action(
            self.request,
            "DELETE",
            instance,
            object_id=object_id,
        )


class BankAccountViewSet(viewsets.ModelViewSet):

    """
    /api/v1/accounts/        GET, POST
    /api/v1/accounts/{id}/   GET, PUT, PATCH, DELETE

    Note: `balance` is read-only here — it can only be moved
    via transactions (deposit/withdraw/transfer), never edited
    directly, same as the staff UI. Branch-scoped like the
    staff UI.

    Filtering: ?account_type=SAVINGS&status=ACTIVE&branch=<id>&customer=<id>
    Search: ?search=<account number>
    Ordering: ?ordering=balance or ?ordering=-created_at
    """

    serializer_class = BankAccountSerializer
    permission_classes = [StrictDjangoModelPermissions]

    filterset_fields = ["account_type", "status", "branch", "customer"]
    search_fields = ["account_number"]
    ordering_fields = ["balance", "created_at"]

    def get_queryset(self):

        queryset = (
            BankAccount.objects
            .select_related("customer")
            .all()
            .order_by("-created_at")
        )

        return scope_to_branch(queryset, self.request.user)

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)

    def perform_update(self, serializer):

        instance = serializer.save()

        log_action(self.request, "UPDATE", instance)

    def perform_destroy(self, instance):

        object_id = str(instance.id)

        instance.delete()

        log_action(
            self.request,
            "DELETE",
            instance,
            object_id=object_id,
        )


class TransactionViewSet(viewsets.ModelViewSet):

    """
    /api/v1/transactions/        GET, POST
    /api/v1/transactions/{id}/   GET

    POST creates a real DEPOSIT/WITHDRAW/TRANSFER using the
    same locking + balance logic as the staff UI (see
    TransactionSerializer.create). Transactions cannot be
    edited or deleted through the API — use the staff UI's
    non-destructive reversal flow for that instead. Branch-
    scoped like the staff UI.

    Filtering: ?transaction_type=DEPOSIT&status=COMPLETED&account=<id>
    Search: ?search=<reference>
    Ordering: ?ordering=-created_at or ?ordering=amount
    """

    http_method_names = ["get", "post", "head", "options"]

    serializer_class = TransactionSerializer
    permission_classes = [StrictDjangoModelPermissions]

    filterset_fields = ["transaction_type", "status", "account"]
    search_fields = ["reference"]
    ordering_fields = ["amount", "created_at"]

    def get_queryset(self):

        queryset = (
            Transaction.objects
            .select_related("account", "destination_account")
            .all()
            .order_by("-created_at")
        )

        return scope_to_branch(
            queryset, self.request.user, branch_field="account__branch",
        )

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)


class StandingOrderViewSet(viewsets.ModelViewSet):

    """
    /api/v1/standing-orders/        GET, POST
    /api/v1/standing-orders/{id}/   GET, PUT, PATCH, DELETE

    Filtering: ?is_active=true&frequency=MONTHLY&account=<id>
    Ordering: ?ordering=next_run_date
    """

    serializer_class = StandingOrderSerializer
    permission_classes = [StrictDjangoModelPermissions]

    filterset_fields = ["is_active", "frequency", "account"]
    ordering_fields = ["next_run_date", "amount"]

    def get_queryset(self):

        queryset = (
            StandingOrder.objects
            .select_related("account", "destination_account")
            .all()
            .order_by("next_run_date")
        )

        return scope_to_branch(
            queryset, self.request.user, branch_field="account__branch",
        )

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)

    def perform_update(self, serializer):

        instance = serializer.save()

        log_action(self.request, "UPDATE", instance)

    def perform_destroy(self, instance):

        object_id = str(instance.id)

        instance.delete()

        log_action(
            self.request,
            "DELETE",
            instance,
            object_id=object_id,
        )


class LoanViewSet(viewsets.ModelViewSet):

    """
    /api/v1/loans/        GET, POST
    /api/v1/loans/{id}/   GET, PUT, PATCH, DELETE

    Filtering: ?status=ACTIVE&account=<id>
    Ordering: ?ordering=-created_at or ?ordering=principal
    """

    serializer_class = LoanSerializer
    permission_classes = [StrictDjangoModelPermissions]

    filterset_fields = ["status", "account"]
    ordering_fields = ["principal", "created_at"]

    def get_queryset(self):

        queryset = (
            Loan.objects
            .select_related("account", "account__customer")
            .all()
            .order_by("-created_at")
        )

        return scope_to_branch(
            queryset, self.request.user, branch_field="account__branch",
        )

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)

    def perform_update(self, serializer):

        instance = serializer.save()

        log_action(self.request, "UPDATE", instance)

    def perform_destroy(self, instance):

        object_id = str(instance.id)

        instance.delete()

        log_action(
            self.request,
            "DELETE",
            instance,
            object_id=object_id,
        )
