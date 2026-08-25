from rest_framework import serializers

from .models import (
    Customer,
    BankAccount,
    Transaction,
    StandingOrder,
    Loan,
)
from .utils import (
    generate_transaction_reference,
    calculate_emi,
)


class CustomerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Customer

        fields = [
            "id",
            "name",
            "email",
            "phone",
            "address",
            "created_at",
        ]

        read_only_fields = ["id", "created_at"]


class BankAccountSerializer(serializers.ModelSerializer):

    customer_name = serializers.CharField(
        source="customer.name",
        read_only=True,
    )

    class Meta:
        model = BankAccount

        fields = [
            "id",
            "account_number",
            "customer",
            "customer_name",
            "account_type",
            "balance",
            "status",
            "interest_rate",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "balance",
            "created_at",
        ]


class TransactionSerializer(serializers.ModelSerializer):

    account_number = serializers.CharField(
        source="account.account_number",
        read_only=True,
    )

    destination_account_number = serializers.CharField(
        source="destination_account.account_number",
        read_only=True,
    )

    class Meta:
        model = Transaction

        fields = [
            "id",
            "reference",
            "account",
            "account_number",
            "destination_account",
            "destination_account_number",
            "transaction_type",
            "amount",
            "balance_after",
            "status",
            "description",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "reference",
            "balance_after",
            "status",
            "created_at",
        ]

    def validate(self, data):

        transaction_type = data.get("transaction_type")
        destination_account = data.get("destination_account")
        account = data.get("account")

        if transaction_type == "TRANSFER":

            if not destination_account:

                raise serializers.ValidationError(
                    "destination_account is required for transfers."
                )

            if destination_account == account:

                raise serializers.ValidationError(
                    "Source and destination accounts cannot be the same."
                )

        if transaction_type == "INTEREST":

            raise serializers.ValidationError(
                "INTEREST transactions are created only by the "
                "credit_interest management command."
            )

        return data

    def create(self, validated_data):

        from django.db import transaction as db_transaction

        transaction_type = validated_data["transaction_type"]
        account = validated_data["account"]
        destination_account = validated_data.get("destination_account")
        amount = validated_data["amount"]
        description = validated_data.get("description", "")

        with db_transaction.atomic():

            locked_account = (
                BankAccount.objects
                .select_for_update()
                .get(id=account.id)
            )

            if locked_account.status != "ACTIVE":

                raise serializers.ValidationError(
                    f"Account {locked_account.account_number} "
                    "is not active."
                )

            if transaction_type == "DEPOSIT":

                locked_account.balance += amount
                locked_account.save(update_fields=["balance"])

            elif transaction_type == "WITHDRAW":

                if locked_account.balance < amount:

                    raise serializers.ValidationError(
                        "Insufficient balance."
                    )

                locked_account.balance -= amount
                locked_account.save(update_fields=["balance"])

            elif transaction_type == "TRANSFER":

                if locked_account.balance < amount:

                    raise serializers.ValidationError(
                        "Insufficient balance."
                    )

                locked_destination = (
                    BankAccount.objects
                    .select_for_update()
                    .get(id=destination_account.id)
                )

                if locked_destination.status != "ACTIVE":

                    raise serializers.ValidationError(
                        f"Destination account "
                        f"{locked_destination.account_number} "
                        "is not active."
                    )

                locked_account.balance -= amount
                locked_destination.balance += amount

                locked_account.save(update_fields=["balance"])
                locked_destination.save(update_fields=["balance"])

            txn = Transaction.objects.create(
                account=locked_account,
                destination_account=(
                    destination_account
                    if transaction_type == "TRANSFER"
                    else None
                ),
                transaction_type=transaction_type,
                amount=amount,
                balance_after=locked_account.balance,
                reference=generate_transaction_reference(
                    locked_account,
                ),
                description=description,
                status="COMPLETED",
            )

            return txn


class StandingOrderSerializer(serializers.ModelSerializer):

    class Meta:
        model = StandingOrder

        fields = [
            "id",
            "account",
            "destination_account",
            "amount",
            "frequency",
            "description",
            "next_run_date",
            "is_active",
            "created_at",
        ]

        read_only_fields = ["id", "created_at"]

    def validate(self, data):

        account = data.get("account")
        destination_account = data.get("destination_account")

        if account and destination_account and account == destination_account:

            raise serializers.ValidationError(
                "Source and destination accounts cannot be the same."
            )

        return data


class LoanSerializer(serializers.ModelSerializer):

    account_number = serializers.CharField(
        source="account.account_number",
        read_only=True,
    )

    emi = serializers.SerializerMethodField()

    class Meta:
        model = Loan

        fields = [
            "id",
            "account",
            "account_number",
            "principal",
            "annual_rate",
            "months",
            "start_date",
            "status",
            "emi",
            "created_at",
        ]

        read_only_fields = ["id", "status", "created_at"]

    def get_emi(self, obj):

        return calculate_emi(
            obj.principal,
            obj.annual_rate,
            obj.months,
        )
