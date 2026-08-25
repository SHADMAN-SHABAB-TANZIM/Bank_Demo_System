from rest_framework import viewsets
from rest_framework.permissions import DjangoModelPermissions

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
    /api/customers/        GET, POST
    /api/customers/{id}/   GET, PUT, PATCH, DELETE

    Uses the same accounts.* Django permissions as the
    staff UI (add_customer, change_customer, delete_customer,
    view_customer).
    """

    queryset = Customer.objects.all().order_by("name")
    serializer_class = CustomerSerializer
    permission_classes = [StrictDjangoModelPermissions]

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
    /api/accounts/        GET, POST
    /api/accounts/{id}/   GET, PUT, PATCH, DELETE

    Note: `balance` is read-only here — it can only be moved
    via transactions (deposit/withdraw/transfer), never edited
    directly, same as the staff UI.
    """

    queryset = (
        BankAccount.objects
        .select_related("customer")
        .all()
        .order_by("-created_at")
    )
    serializer_class = BankAccountSerializer
    permission_classes = [StrictDjangoModelPermissions]

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
    /api/transactions/        GET, POST
    /api/transactions/{id}/   GET

    POST creates a real DEPOSIT/WITHDRAW/TRANSFER using the
    same locking + balance logic as the staff UI (see
    TransactionSerializer.create). Transactions cannot be
    edited or deleted through the API — use the staff UI's
    reversal-on-delete flow for that instead, to keep the
    audit story simple.
    """

    http_method_names = ["get", "post", "head", "options"]

    queryset = (
        Transaction.objects
        .select_related("account", "destination_account")
        .all()
        .order_by("-created_at")
    )
    serializer_class = TransactionSerializer
    permission_classes = [StrictDjangoModelPermissions]

    def perform_create(self, serializer):

        instance = serializer.save()

        log_action(self.request, "CREATE", instance)


class StandingOrderViewSet(viewsets.ModelViewSet):

    """
    /api/standing-orders/        GET, POST
    /api/standing-orders/{id}/   GET, PUT, PATCH, DELETE
    """

    queryset = (
        StandingOrder.objects
        .select_related("account", "destination_account")
        .all()
        .order_by("next_run_date")
    )
    serializer_class = StandingOrderSerializer
    permission_classes = [StrictDjangoModelPermissions]

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
    /api/loans/        GET, POST
    /api/loans/{id}/   GET, PUT, PATCH, DELETE
    """

    queryset = (
        Loan.objects
        .select_related("account", "account__customer")
        .all()
        .order_by("-created_at")
    )
    serializer_class = LoanSerializer
    permission_classes = [StrictDjangoModelPermissions]

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
