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
        ('LOAN_DISBURSEMENT', 'Loan Disbursement'),
        ('LOAN_REPAYMENT', 'Loan Repayment'),
        ('REVERSAL', 'Reversal'),
    ]

    STATUS_CHOICES = [
        ('COMPLETED', 'Completed'),
        ('PENDING', 'Pending'),
        ('FAILED', 'Failed'),
        ('REVERSED', 'Reversed'),
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

    fee_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Fee charged on top of amount, if any (see FeeRule).",
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

    reverses = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reversed_by',
        help_text=(
            "If this is a compensating REVERSAL transaction, "
            "points to the original transaction it reverses. "
            "OneToOne so an original can only be reversed once."
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.reference} - {self.transaction_type} - {self.amount}"

class ChartOfAccount(models.Model):

    """
    The internal accounting ledger's chart of accounts - NOT
    to be confused with BankAccount (which is a customer's
    account). Every financial movement posts balanced debit/
    credit lines against these accounts, per standard
    double-entry bookkeeping for a bank:

    - ASSET: what the bank owns (cash, loans receivable).
      Normal balance: DEBIT.
    - LIABILITY: what the bank owes (customer deposits - a
      depositor's balance is a liability TO the bank, not an
      asset of theirs). Normal balance: CREDIT.
    - INCOME: interest earned on loans, fees collected.
      Normal balance: CREDIT.
    - EXPENSE: interest paid to depositors. Normal balance:
      DEBIT.
    """

    ACCOUNT_TYPES = [
        ('ASSET', 'Asset'),
        ('LIABILITY', 'Liability'),
        ('EQUITY', 'Equity'),
        ('INCOME', 'Income'),
        ('EXPENSE', 'Expense'),
    ]

    NORMAL_BALANCE_BY_TYPE = {
        'ASSET': 'DEBIT',
        'EXPENSE': 'DEBIT',
        'LIABILITY': 'CREDIT',
        'EQUITY': 'CREDIT',
        'INCOME': 'CREDIT',
    }

    code = models.CharField(max_length=20, unique=True)

    name = models.CharField(max_length=100)

    account_type = models.CharField(
        max_length=10,
        choices=ACCOUNT_TYPES,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def normal_balance(self):
        return self.NORMAL_BALANCE_BY_TYPE[self.account_type]


class JournalEntry(models.Model):

    """
    One balanced accounting event - the header for a set of
    JournalLine debit/credit rows whose totals must be equal.
    Usually linked back to the customer-facing Transaction
    that caused it (source_transaction), but can stand alone
    for pure book entries (e.g. opening balances).
    """

    reference = models.CharField(max_length=50, unique=True)

    description = models.CharField(max_length=255, blank=True)

    source_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name='journal_entries',
        null=True,
        blank=True,
        help_text=(
            "The customer-facing Transaction that caused this "
            "entry, if any. Deleting that Transaction cascades "
            "to remove this entry and its lines too, keeping "
            "the books consistent with the reversal."
        ),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.reference


class JournalLine(models.Model):

    """
    One debit or credit line within a JournalEntry. Exactly
    one of debit/credit should be non-zero per line (never
    both) - that's enforced by accounts.ledger.post_journal_entry
    rather than a DB constraint, so the validation error message
    can be specific.
    """

    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name='lines',
    )

    account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        related_name='journal_lines',
    )

    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        related_name='journal_lines',
        null=True,
        blank=True,
        help_text=(
            "The specific customer account this line relates "
            "to, when the ChartOfAccount is a customer-deposit "
            "liability account. Blank for bank-internal lines "
            "like Cash or Interest Income/Expense."
        ),
    )

    debit = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
    )

    credit = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
    )

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.code} {side}"



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


class LoanInstallment(models.Model):

    """
    One row of a loan's persisted amortization schedule,
    generated at disbursement time from
    accounts.utils.amortization_schedule so payment status can
    actually be tracked (the on-the-fly EMI calculator has no
    concept of "paid" - this does).
    """

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('OVERDUE', 'Overdue'),
    ]

    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name='installments',
    )

    installment_no = models.PositiveIntegerField()

    due_date = models.DateField()

    principal_due = models.DecimalField(max_digits=15, decimal_places=2)

    interest_due = models.DecimalField(max_digits=15, decimal_places=2)

    total_due = models.DecimalField(max_digits=15, decimal_places=2)

    penalty_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
    )

    amount_paid = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
    )

    paid_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='PENDING',
    )

    class Meta:
        ordering = ['loan', 'installment_no']
        unique_together = [('loan', 'installment_no')]

    def __str__(self):
        return f"Loan #{self.loan_id} installment {self.installment_no}"


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


class FeeRule(models.Model):

    """
    A configurable fee applied to a given transaction type.
    Only one active rule per transaction_type is looked up
    (accounts.utils.calculate_fee takes the first active
    match). Ships inactive by default via seed_fee_rules -
    activating one is an explicit admin action, so existing
    withdraw/transfer behavior is unaffected until a manager
    opts in.
    """

    FEE_TRANSACTION_TYPES = [
        ('WITHDRAW', 'Withdrawal'),
        ('TRANSFER', 'Transfer'),
    ]

    FEE_TYPES = [
        ('FLAT', 'Flat Amount'),
        ('PERCENTAGE', 'Percentage of Amount'),
    ]

    name = models.CharField(max_length=100)

    transaction_type = models.CharField(
        max_length=20,
        choices=FEE_TRANSACTION_TYPES,
    )

    fee_type = models.CharField(
        max_length=12,
        choices=FEE_TYPES,
        default='FLAT',
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text=(
            "For FLAT: the fee in taka. For PERCENTAGE: the "
            "rate, e.g. 1.50 means 1.5% of the transaction amount."
        ),
    )

    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['transaction_type', 'name']

    def __str__(self):

        if self.fee_type == 'FLAT':
            rate = f"৳{self.amount}"
        else:
            rate = f"{self.amount}%"

        return f"{self.name} ({self.get_transaction_type_display()}, {rate})"


class CustomerPortalAccount(models.Model):

    """
    Links a Django User (login credentials) to a Customer, so
    that customer can log into the self-service portal and see
    only their own accounts/transactions/loans. Created by
    staff via the "Enable Portal Access" action on a customer's
    detail page (accounts.views.customer_portal_enable) - there
    is no self-registration flow.

    A User with a linked CustomerPortalAccount is never given
    is_staff=True, so they're automatically locked out of
    /admin/ and every staff view (which all require Django
    model permissions this account is never granted).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='customer_profile',
    )

    customer = models.OneToOneField(
        Customer,
        on_delete=models.CASCADE,
        related_name='portal_account',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Portal account for {self.customer.name}"


class FraudAlert(models.Model):

    """
    A rule-based fraud flag on a transaction. Flagging is
    purely additive - it never blocks or delays the underlying
    transaction (see accounts.fraud), only surfaces it for
    staff review, matching how most real banking systems layer
    post-transaction monitoring rather than pre-transaction
    blocking for a first-pass rule engine.
    """

    STATUS_CHOICES = [
        ('PENDING_REVIEW', 'Pending Review'),
        ('CONFIRMED_FRAUD', 'Confirmed Fraud'),
        ('FALSE_POSITIVE', 'False Positive'),
    ]

    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name='fraud_alerts',
    )

    reason = models.TextField(
        help_text="Which rule(s) matched and why, for the reviewer.",
    )

    risk_score = models.PositiveIntegerField(
        help_text="0-100, higher = more suspicious.",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING_REVIEW',
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fraud_alerts_reviewed',
    )

    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-risk_score', '-created_at']

    def __str__(self):
        return (
            f"Alert on {self.transaction.reference} "
            f"(score {self.risk_score}, {self.get_status_display()})"
        )
