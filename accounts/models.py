from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator

from .fields import EncryptedCharField


class Branch(models.Model):

    """
    A physical branch. Customers, accounts, and employees all
    associate with a branch - Branch Managers and Tellers are
    scoped to see only their own branch's data (enforced in
    views via accounts.branch_scope.branch_queryset), while
    Super Admin / System Administrator / Auditor roles see
    everything regardless of branch.
    """

    name = models.CharField(max_length=100)

    code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Short branch code, e.g. DHK-01",
    )

    address = models.CharField(max_length=255, blank=True)

    phone = models.CharField(max_length=20, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeProfile(models.Model):

    """
    Extends Django's built-in User with the organizational
    role and branch assignment from the roadmap's multi-level
    user system. This is separate from Django's permission
    Groups (B_VIEWER/TELLER/OFFICER/MANAGER/ADMIN, set up by
    setup_roles): the Group controls *what actions* a user can
    perform (Django permissions), while EmployeeProfile.role
    and .branch control *organizational identity* and *which
    branch's data* they can see. A user typically has both:
    a Group for permissions, and an EmployeeProfile for role
    display + branch scoping.
    """

    ROLE_CHOICES = [
        ("SUPER_ADMIN", "Super Admin"),
        ("SYSTEM_ADMIN", "System Administrator"),
        ("BRANCH_MANAGER", "Branch Manager"),
        ("BANK_OFFICER", "Bank Officer"),
        ("TELLER", "Teller"),
        ("LOAN_OFFICER", "Loan Officer"),
        ("AUDITOR", "Auditor"),
    ]

    # Roles that see data across ALL branches rather than
    # being scoped to a single one.
    UNSCOPED_ROLES = ["SUPER_ADMIN", "SYSTEM_ADMIN", "AUDITOR"]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="TELLER",
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="employees",
        null=True,
        blank=True,
        help_text=(
            "Leave blank for Super Admin / System Administrator "
            "/ Auditor roles, which are not tied to one branch."
        ),
    )

    employee_id = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_branch_scoped(self):
        return self.role not in self.UNSCOPED_ROLES


class Customer(models.Model):

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="customers",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=100)

    email = models.EmailField(unique=True)

    phone = EncryptedCharField(max_length=255, blank=True)

    nid = EncryptedCharField(
        max_length=255,
        blank=True,
        verbose_name="National ID",
        help_text="Encrypted at rest, same as NID/PHONE in the Oracle schema.",
    )

    address = models.CharField(max_length=255)

    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Deactivated customers are hidden from normal use "
            "but kept for record-keeping, instead of being "
            "hard-deleted."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class BankAccount(models.Model):

    ACCOUNT_TYPES = [
        ('SAVINGS', 'Savings'),
        ('CURRENT', 'Current'),
    ]

    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('BLOCKED', 'Blocked'),
        ('CLOSED', 'Closed'),
    ]

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='bank_accounts'
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name='bank_accounts',
        null=True,
        blank=True,
        help_text="Branch where this account is held. Defaults to the customer's branch if left blank.",
    )

    account_number = models.CharField(
        max_length=20,
        unique=True
    )

    account_type = models.CharField(
        max_length=20,
        choices=ACCOUNT_TYPES
    )

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='ACTIVE'
    )

    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=4.00,
        help_text=(
            'Annual interest rate (%) applied to SAVINGS '
            'accounts by the credit_interest command. '
            'Ignored for CURRENT accounts.'
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.account_number

    def save(self, *args, **kwargs):

        if self.branch_id is None and self.customer_id:
            self.branch_id = self.customer.branch_id

        super().save(*args, **kwargs)


class Transaction(models.Model):

    TRANSACTION_TYPES = [
        ('DEPOSIT', 'Deposit'),
        ('WITHDRAW', 'Withdrawal'),
        ('TRANSFER', 'Transfer'),
        ('INTEREST', 'Interest'),
    ]

    STATUS_CHOICES = [
        ('COMPLETED', 'Completed'),
        ('PENDING', 'Pending'),
        ('FAILED', 'Failed'),
    ]

    account = models.ForeignKey(
        BankAccount,
        on_delete=models.PROTECT,
        related_name='transactions'
    )

    destination_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        related_name='incoming_transfers',
        null=True,
        blank=True
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01"))
        ]
    )

    balance_after = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=50,
        unique=True
    )

    description = models.CharField(
        max_length=255,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='COMPLETED'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.reference} - {self.transaction_type} - {self.amount}"

class StandingOrder(models.Model):

    """
    A recurring transfer instruction. The
    `run_standing_orders` management command finds every
    active order whose `next_run_date` has arrived, executes
    it as a real TRANSFER transaction (reusing the same
    balance-locking logic as a manual transfer), and advances
    `next_run_date` by `frequency`.
    """

    FREQUENCY_CHOICES = [
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
    ]

    account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='standing_orders',
    )

    destination_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='incoming_standing_orders',
    )

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01"))
        ],
    )

    frequency = models.CharField(
        max_length=10,
        choices=FREQUENCY_CHOICES,
        default='MONTHLY',
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    next_run_date = models.DateField()

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"{self.account.account_number} -> "
            f"{self.destination_account.account_number} "
            f"({self.amount}, {self.frequency})"
        )


class AuditLog(models.Model):

    """
    A simple append-only trail of who did what. Entries are
    written explicitly from views (and management commands)
    via accounts.utils.log_action, rather than through
    signals, so the acting user is always known.
    """

    ACTION_CHOICES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )

    action = models.CharField(
        max_length=10,
        choices=ACTION_CHOICES,
    )

    model_name = models.CharField(max_length=50)

    object_id = models.CharField(max_length=50)

    object_repr = models.CharField(max_length=255)

    note = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return (
            f"{self.action} {self.model_name} "
            f"#{self.object_id} by "
            f"{self.user or 'system'}"
        )


class Loan(models.Model):

    """
    A simple installment loan against an account, with EMI
    (equal monthly installment) calculated the same way as
    the calc_emi PL/SQL function in the original Oracle
    project: standard reducing-balance amortization.
    """

    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('CLOSED', 'Closed'),
        ('DEFAULTED', 'Defaulted'),
    ]

    account = models.ForeignKey(
        BankAccount,
        on_delete=models.PROTECT,
        related_name='loans',
    )

    principal = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("1.00"))
        ],
    )

    annual_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Annual interest rate (%)",
    )

    months = models.PositiveIntegerField(
        help_text="Loan term in months",
    )

    start_date = models.DateField()

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='ACTIVE',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"Loan #{self.id} - {self.account.account_number} "
            f"- {self.principal}"
        )


class DailySnapshot(models.Model):

    """
    A once-daily rollup of system-wide stats, mirroring
    generate_daily_report / daily_report_view from the
    Oracle project. Written by the generate_daily_snapshot
    management command; used to power the trend chart on the
    dashboard.
    """

    date = models.DateField(unique=True)

    customers_count = models.PositiveIntegerField(default=0)
    accounts_count = models.PositiveIntegerField(default=0)
    active_accounts = models.PositiveIntegerField(default=0)
    inactive_accounts = models.PositiveIntegerField(default=0)

    transactions_count = models.PositiveIntegerField(default=0)

    total_deposits = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )
    total_withdrawals = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )
    total_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"Snapshot {self.date}"
