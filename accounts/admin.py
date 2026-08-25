from django.contrib import admin
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

admin.site.register(Customer)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):

    list_display = ('code', 'name', 'phone', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):

    list_display = ('user', 'role', 'branch', 'employee_id')
    list_filter = ('role', 'branch')
    search_fields = ('user__username', 'employee_id')


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'account',
        'principal',
        'annual_rate',
        'months',
        'start_date',
        'status',
    )

    list_filter = (
        'status',
    )

    search_fields = (
        'account__account_number',
    )


@admin.register(DailySnapshot)
class DailySnapshotAdmin(admin.ModelAdmin):

    list_display = (
        'date',
        'customers_count',
        'accounts_count',
        'transactions_count',
        'total_deposits',
        'total_withdrawals',
        'total_balance',
    )

    ordering = ('-date',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):

    list_display = (
        'account_number',
        'customer',
        'account_type',
        'balance',
        'status',
        'created_at',
    )

    list_filter = (
        'account_type',
        'status',
    )

    search_fields = (
        'account_number',
        'customer__name',
    )


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):

    list_display = (
        'reference',
        'account',
        'transaction_type',
        'amount',
        'balance_after',
        'status',
        'created_at',
    )

    list_filter = (
        'transaction_type',
        'status',
        'created_at',
    )

    search_fields = (
        'reference',
        'account__account_number',
    )

    ordering = (
        '-created_at',
    )

@admin.register(StandingOrder)
class StandingOrderAdmin(admin.ModelAdmin):

    list_display = (
        'account',
        'destination_account',
        'amount',
        'frequency',
        'next_run_date',
        'is_active',
    )

    list_filter = (
        'frequency',
        'is_active',
    )

    search_fields = (
        'account__account_number',
        'destination_account__account_number',
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):

    list_display = (
        'created_at',
        'user',
        'action',
        'model_name',
        'object_id',
        'object_repr',
    )

    list_filter = (
        'action',
        'model_name',
    )

    search_fields = (
        'object_repr',
        'user__username',
    )

    ordering = (
        '-created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
