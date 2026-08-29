from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import (
    AuditLog,
    BankAccount,
    Branch,
    ChartOfAccount,
    Customer,
    CustomerPortalAccount,
    DailySnapshot,
    EmployeeProfile,
    FeeRule,
    FraudAlert,
    JournalEntry,
    JournalLine,
    Loan,
    StandingOrder,
    Transaction,
)
from .utils import amortization_schedule, calculate_emi
from . import ledger


def make_staff_user(username, permissions=None, groups=None):

    """
    Test helper: creates a user with is_staff=True and an
    optional set of permission codenames (e.g. "add_customer")
    and/or group names, so tests can check the same permission
    boundaries the real app enforces.
    """

    user = User.objects.create_user(
        username=username,
        password="testpass123",
        is_staff=True,
    )

    if permissions:

        for codename in permissions:

            perm = Permission.objects.get(
                content_type__app_label="accounts",
                codename=codename,
            )

            user.user_permissions.add(perm)

    if groups:

        for group_name in groups:

            group, _ = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)

    return user


class CustomerModelTests(TestCase):

    def test_customer_str_is_name(self):

        customer = Customer.objects.create(
            name="Rahim Uddin",
            email="rahim@example.com",
            phone="01700000000",
            address="Dhaka",
        )

        self.assertEqual(str(customer), "Rahim Uddin")

    def test_customer_defaults_active(self):

        customer = Customer.objects.create(
            name="Karim",
            email="karim@example.com",
            phone="01700000001",
            address="Dhaka",
        )

        self.assertTrue(customer.is_active)

    def test_phone_is_encrypted_or_stored_gracefully(self):

        """
        Whether or not `cryptography` is installed in the test
        environment, saving and re-reading a customer's phone
        through the ORM must round-trip correctly.
        """

        customer = Customer.objects.create(
            name="Nazma",
            email="nazma@example.com",
            phone="01711111111",
            address="Dhaka",
        )

        customer.refresh_from_db()

        self.assertEqual(customer.phone, "01711111111")


class TransactionBusinessLogicTests(TestCase):

    """
    These exercise the deposit/withdraw/transfer/delete views
    directly - the core money-moving logic of the app.
    """

    def setUp(self):

        self.user = make_staff_user(
            "teller",
            permissions=[
                "view_customer", "add_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
                "change_transaction", "delete_transaction",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            name="Test Customer",
            email="testcust@example.com",
            phone="01700000002",
            address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=self.customer,
            account_number="TEST0001",
            account_type="SAVINGS",
            balance=Decimal("1000.00"),
            status="ACTIVE",
        )

        self.account2 = BankAccount.objects.create(
            customer=self.customer,
            account_number="TEST0002",
            account_type="SAVINGS",
            balance=Decimal("500.00"),
            status="ACTIVE",
        )

    def test_deposit_increases_balance(self):

        response = self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "DEPOSIT",
                "amount": "250.00",
                "description": "Test deposit",
            },
        )

        self.account.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account.balance, Decimal("1250.00"))

        txn = Transaction.objects.filter(
            account=self.account,
            transaction_type="DEPOSIT",
        ).first()

        self.assertIsNotNone(txn)
        self.assertEqual(txn.balance_after, Decimal("1250.00"))

    def test_withdraw_decreases_balance(self):

        response = self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "WITHDRAW",
                "amount": "300.00",
                "description": "Test withdrawal",
            },
        )

        self.account.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account.balance, Decimal("700.00"))

    def test_withdraw_more_than_balance_fails(self):

        response = self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "WITHDRAW",
                "amount": "999999.00",
                "description": "Overdraw attempt",
            },
        )

        self.account.refresh_from_db()

        # Form re-renders with an error rather than redirecting
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account.balance, Decimal("1000.00"))

        self.assertFalse(
            Transaction.objects.filter(
                account=self.account,
                transaction_type="WITHDRAW",
            ).exists()
        )

    def test_transfer_moves_money_between_accounts(self):

        response = self.client.post(
            reverse("transaction_transfer"),
            {
                "account": self.account.id,
                "destination_account": self.account2.id,
                "transaction_type": "TRANSFER",
                "amount": "200.00",
                "description": "Test transfer",
            },
        )

        self.account.refresh_from_db()
        self.account2.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account.balance, Decimal("800.00"))
        self.assertEqual(self.account2.balance, Decimal("700.00"))

    def test_transfer_to_same_account_rejected(self):

        response = self.client.post(
            reverse("transaction_transfer"),
            {
                "account": self.account.id,
                "destination_account": self.account.id,
                "transaction_type": "TRANSFER",
                "amount": "50.00",
            },
        )

        self.account.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_reversing_latest_transaction_undoes_balance_non_destructively(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "DEPOSIT",
                "amount": "100.00",
            },
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1100.00"))

        txn = Transaction.objects.filter(account=self.account).latest("id")

        response = self.client.post(
            reverse("transaction_reverse", args=[txn.id]),
        )

        self.account.refresh_from_db()
        txn.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account.balance, Decimal("1000.00"))

        # Non-destructive: the original row is kept, just marked
        # REVERSED, and a new REVERSAL transaction exists.
        self.assertTrue(
            Transaction.objects.filter(id=txn.id).exists()
        )
        self.assertEqual(txn.status, "REVERSED")
        self.assertTrue(
            Transaction.objects.filter(
                reverses=txn, transaction_type="REVERSAL",
            ).exists()
        )


class CustomerLifecycleTests(TestCase):

    def setUp(self):

        self.user = make_staff_user(
            "manager",
            permissions=[
                "view_customer", "add_customer",
                "change_customer", "delete_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_customer_delete_blocked_when_has_transaction_history(self):

        customer = Customer.objects.create(
            name="Protected Customer",
            email="protected@example.com",
            phone="01700000003",
            address="Dhaka",
        )

        account = BankAccount.objects.create(
            customer=customer,
            account_number="PROT0001",
            account_type="SAVINGS",
            balance=Decimal("100.00"),
            status="ACTIVE",
        )

        Transaction.objects.create(
            account=account,
            transaction_type="DEPOSIT",
            amount=Decimal("100.00"),
            balance_after=Decimal("100.00"),
            reference="TXN-PROT0001-TEST0001",
            status="COMPLETED",
        )

        response = self.client.post(
            reverse("customer_delete", args=[customer.id]),
        )

        # Should NOT redirect (blocked) - re-renders with error
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Customer.objects.filter(id=customer.id).exists()
        )

    def test_customer_deactivate_hides_from_default_list(self):

        customer = Customer.objects.create(
            name="Soon Inactive",
            email="inactive@example.com",
            phone="01700000004",
            address="Dhaka",
        )

        self.client.post(
            reverse("customer_deactivate", args=[customer.id]),
        )

        customer.refresh_from_db()
        self.assertFalse(customer.is_active)

        response = self.client.get(reverse("customer_list"))

        # Check the actual customer list context, not the whole
        # page - the flash message legitimately mentions the
        # customer's name too ("Customer 'Soon Inactive'
        # deactivated."), so whole-page string matching would
        # give a false failure.
        listed_ids = [c.id for c in response.context["customers"]]
        self.assertNotIn(customer.id, listed_ids)

    def test_customer_reactivate_restores_visibility(self):

        customer = Customer.objects.create(
            name="Coming Back",
            email="comeback@example.com",
            phone="01700000005",
            address="Dhaka",
            is_active=False,
        )

        self.client.post(
            reverse("customer_reactivate", args=[customer.id]),
        )

        customer.refresh_from_db()
        self.assertTrue(customer.is_active)


class RolePermissionTests(TestCase):

    """
    Confirms the B_VIEWER/TELLER/OFFICER/MANAGER/ADMIN role
    groups actually enforce the access boundaries they're
    supposed to.
    """

    def setUp(self):

        # Mirror the smallest slice of setup_roles needed here,
        # so this test doesn't depend on that command having
        # been run.
        viewer_group, _ = Group.objects.get_or_create(name="B_VIEWER")

        view_customer_perm = Permission.objects.get(
            content_type__app_label="accounts",
            codename="view_customer",
        )

        viewer_group.permissions.add(view_customer_perm)

        self.viewer = User.objects.create_user(
            username="vieweronly",
            password="testpass123",
            is_staff=True,
        )
        self.viewer.groups.add(viewer_group)

        self.client = Client()
        self.client.force_login(self.viewer)

    def test_viewer_can_view_customer_list(self):

        response = self.client.get(reverse("customer_list"))
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_create_customer(self):

        response = self.client.post(
            reverse("customer_create"),
            {
                "name": "Should Not Exist",
                "email": "blocked@example.com",
                "phone": "01700000006",
                "address": "Dhaka",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Customer.objects.filter(name="Should Not Exist").exists()
        )

    def test_unauthenticated_user_redirected_to_login(self):

        anon_client = Client()

        response = anon_client.get(reverse("customer_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class AuditLogTests(TestCase):

    def setUp(self):

        self.user = make_staff_user(
            "audituser",
            permissions=["view_customer", "add_customer"],
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_customer_create_writes_audit_log(self):

        self.assertEqual(AuditLog.objects.count(), 0)

        self.client.post(
            reverse("customer_create"),
            {
                "name": "Audited Customer",
                "email": "audited@example.com",
                "phone": "01700000007",
                "address": "Dhaka",
            },
        )

        log = AuditLog.objects.filter(model_name="Customer").first()

        self.assertIsNotNone(log)
        self.assertEqual(log.action, "CREATE")
        self.assertEqual(log.user, self.user)


class EmiCalculationTests(TestCase):

    """
    Verifies calculate_emi / amortization_schedule against a
    manually-computed reducing-balance formula, independent of
    the application code.
    """

    def test_emi_matches_manual_formula(self):

        principal = Decimal("100000")
        annual_rate = Decimal("12.00")
        months = 12

        emi = calculate_emi(principal, annual_rate, months)

        monthly_rate = 0.01
        factor = (1 + monthly_rate) ** months
        expected = principal * Decimal(str(monthly_rate)) * Decimal(str(factor)) / Decimal(str(factor - 1))

        self.assertAlmostEqual(
            float(emi), float(expected.quantize(Decimal("0.01"))), places=1,
        )

    def test_emi_zero_rate_is_straight_division(self):

        emi = calculate_emi(Decimal("12000"), Decimal("0"), 12)

        self.assertEqual(emi, Decimal("1000.00"))

    def test_amortization_schedule_closes_to_zero(self):

        schedule = amortization_schedule(
            Decimal("50000"), Decimal("10.00"), 6, date(2026, 1, 1),
        )

        self.assertEqual(len(schedule), 6)
        self.assertEqual(schedule[-1]["closing_balance"], Decimal("0.00"))

    def test_amortization_schedule_principal_sums_to_loan_amount(self):

        principal = Decimal("75000")

        schedule = amortization_schedule(
            principal, Decimal("8.50"), 9, date(2026, 3, 1),
        )

        total_principal = sum(row["principal"] for row in schedule)

        self.assertEqual(total_principal, principal)


class LoanViewTests(TestCase):

    def setUp(self):

        self.user = make_staff_user(
            "loanofficer",
            permissions=[
                "view_bankaccount", "add_bankaccount",
                "view_loan", "add_loan", "change_loan",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Loan Customer",
            email="loancust@example.com",
            phone="01700000008",
            address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer,
            account_number="LOAN0001",
            account_type="CURRENT",
            balance=Decimal("0.00"),
            status="ACTIVE",
        )

    def test_create_loan_and_close_it(self):

        response = self.client.post(
            reverse("loan_create"),
            {
                "account": self.account.id,
                "principal": "20000.00",
                "annual_rate": "9.00",
                "months": "6",
                "start_date": "2026-01-01",
            },
        )

        self.assertEqual(response.status_code, 302)

        loan = Loan.objects.first()
        self.assertIsNotNone(loan)
        self.assertEqual(loan.status, "ACTIVE")

        self.client.post(reverse("loan_close", args=[loan.id]))

        loan.refresh_from_db()
        self.assertEqual(loan.status, "CLOSED")


class StandingOrderCommandTests(TestCase):

    def setUp(self):

        customer = Customer.objects.create(
            name="Standing Order Customer",
            email="so@example.com",
            phone="01700000009",
            address="Dhaka",
        )

        self.acc_from = BankAccount.objects.create(
            customer=customer,
            account_number="SO0001",
            account_type="SAVINGS",
            balance=Decimal("2000.00"),
            status="ACTIVE",
        )

        self.acc_to = BankAccount.objects.create(
            customer=customer,
            account_number="SO0002",
            account_type="SAVINGS",
            balance=Decimal("500.00"),
            status="ACTIVE",
        )

    def test_due_standing_order_executes_and_moves_balance(self):

        from django.core.management import call_command

        order = StandingOrder.objects.create(
            account=self.acc_from,
            destination_account=self.acc_to,
            amount=Decimal("300.00"),
            frequency="MONTHLY",
            next_run_date=date.today(),
            is_active=True,
        )

        call_command("run_standing_orders")

        self.acc_from.refresh_from_db()
        self.acc_to.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(self.acc_from.balance, Decimal("1700.00"))
        self.assertEqual(self.acc_to.balance, Decimal("800.00"))
        self.assertGreater(order.next_run_date, date.today())

    def test_paused_standing_order_does_not_execute(self):

        from django.core.management import call_command

        StandingOrder.objects.create(
            account=self.acc_from,
            destination_account=self.acc_to,
            amount=Decimal("300.00"),
            frequency="MONTHLY",
            next_run_date=date.today(),
            is_active=False,
        )

        call_command("run_standing_orders")

        self.acc_from.refresh_from_db()
        self.assertEqual(self.acc_from.balance, Decimal("2000.00"))


class InterestCommandTests(TestCase):

    def test_credit_interest_adds_correct_amount(self):

        from django.core.management import call_command

        customer = Customer.objects.create(
            name="Interest Customer",
            email="interest@example.com",
            phone="01700000010",
            address="Dhaka",
        )

        account = BankAccount.objects.create(
            customer=customer,
            account_number="INT0001",
            account_type="SAVINGS",
            balance=Decimal("12000.00"),
            interest_rate=Decimal("6.00"),
            status="ACTIVE",
        )

        call_command("credit_interest")

        account.refresh_from_db()

        # 12000 * (6/100/12) = 60.00
        self.assertEqual(account.balance, Decimal("12060.00"))

        txn = Transaction.objects.filter(
            account=account, transaction_type="INTEREST",
        ).first()

        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal("60.00"))


class CsvExportTests(TestCase):

    def setUp(self):

        self.user = make_staff_user(
            "exporter",
            permissions=["view_transaction", "view_bankaccount"],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Export Customer",
            email="export@example.com",
            phone="01700000011",
            address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer,
            account_number="EXP0001",
            account_type="SAVINGS",
            balance=Decimal("500.00"),
            status="ACTIVE",
        )

        Transaction.objects.create(
            account=self.account,
            transaction_type="DEPOSIT",
            amount=Decimal("500.00"),
            balance_after=Decimal("500.00"),
            reference="TXN-EXP0001-TESTREF01",
            status="COMPLETED",
        )

    def test_transaction_export_returns_csv(self):

        response = self.client.get(reverse("transaction_export_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(b"TXN-EXP0001-TESTREF01", response.content)

    def test_account_statement_export_returns_csv(self):

        response = self.client.get(
            reverse("bank_account_statement_csv", args=[self.account.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(b"EXP0001", response.content)


class DailySnapshotCommandTests(TestCase):

    def test_generate_daily_snapshot_creates_record(self):

        from django.core.management import call_command

        customer = Customer.objects.create(
            name="Snapshot Customer",
            email="snapshot@example.com",
            phone="01700000012",
            address="Dhaka",
        )

        BankAccount.objects.create(
            customer=customer,
            account_number="SNAP0001",
            account_type="SAVINGS",
            balance=Decimal("1000.00"),
            status="ACTIVE",
        )

        self.assertEqual(DailySnapshot.objects.count(), 0)

        call_command("generate_daily_snapshot")

        self.assertEqual(DailySnapshot.objects.count(), 1)

        snapshot = DailySnapshot.objects.first()
        self.assertEqual(snapshot.customers_count, 1)
        self.assertEqual(snapshot.accounts_count, 1)
        self.assertEqual(snapshot.total_balance, Decimal("1000.00"))

    def test_generate_daily_snapshot_is_idempotent_for_same_date(self):

        from django.core.management import call_command

        call_command("generate_daily_snapshot")
        call_command("generate_daily_snapshot")

        # Same date should update, not duplicate
        self.assertEqual(DailySnapshot.objects.count(), 1)


class BranchScopingTests(TestCase):

    """
    Verifies the core Priority-1 roadmap feature: branch-scoped
    data access.
    """

    def setUp(self):

        self.hq = Branch.objects.create(name="Head Office", code="HQ-01")
        self.ctg = Branch.objects.create(name="Chittagong", code="CTG-01")

        self.hq_customer = Customer.objects.create(
            name="HQ Customer", email="hqcust@example.com",
            phone="01700000020", address="Dhaka", branch=self.hq,
        )

        self.ctg_customer = Customer.objects.create(
            name="CTG Customer", email="ctgcust@example.com",
            phone="01700000021", address="Chittagong", branch=self.ctg,
        )

        manager_group, _ = Group.objects.get_or_create(name="B_MANAGER")

        view_perm = Permission.objects.get(
            content_type__app_label="accounts", codename="view_customer",
        )
        manager_group.permissions.add(view_perm)

        self.ctg_manager = User.objects.create_user(
            username="ctgmanager", password="testpass123", is_staff=True,
        )
        self.ctg_manager.groups.add(manager_group)
        EmployeeProfile.objects.create(
            user=self.ctg_manager, role="BRANCH_MANAGER", branch=self.ctg,
        )

        self.auditor = User.objects.create_user(
            username="auditor", password="testpass123", is_staff=True,
        )
        self.auditor.groups.add(manager_group)
        EmployeeProfile.objects.create(
            user=self.auditor, role="AUDITOR", branch=None,
        )

    def test_branch_manager_sees_only_own_branch(self):

        client = Client()
        client.force_login(self.ctg_manager)

        response = client.get(reverse("customer_list"))

        self.assertContains(response, "CTG Customer")
        self.assertNotContains(response, "HQ Customer")

    def test_auditor_sees_all_branches(self):

        client = Client()
        client.force_login(self.auditor)

        response = client.get(reverse("customer_list"))

        self.assertContains(response, "CTG Customer")
        self.assertContains(response, "HQ Customer")

    def test_user_without_employee_profile_sees_all_branches(self):

        plain_user = make_staff_user(
            "noprofileuser", permissions=["view_customer"],
        )

        client = Client()
        client.force_login(plain_user)

        response = client.get(reverse("customer_list"))

        self.assertContains(response, "CTG Customer")
        self.assertContains(response, "HQ Customer")

    def test_branch_delete_blocked_when_has_customers(self):

        admin_user = make_staff_user(
            "branchadmin",
            permissions=["view_branch", "delete_branch"],
        )

        client = Client()
        client.force_login(admin_user)

        response = client.post(
            reverse("branch_delete", args=[self.hq.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Branch.objects.filter(id=self.hq.id).exists())

    def test_bank_account_inherits_customer_branch(self):

        account = BankAccount.objects.create(
            customer=self.hq_customer,
            account_number="BRANCHTEST01",
            account_type="SAVINGS",
            balance=Decimal("0.00"),
            status="ACTIVE",
        )

        self.assertEqual(account.branch, self.hq)


class LedgerTests(TestCase):

    """
    Tests for Priority 2 of the roadmap: the double-entry
    ledger. Verifies each transaction type posts a balanced
    journal entry, the trial balance always reconciles, and
    deleting a transaction cleans up its journal entry too.
    """

    def setUp(self):

        from django.core.management import call_command
        call_command("seed_chart_of_accounts")

        self.user = make_staff_user(
            "ledgeruser",
            permissions=[
                "view_customer", "add_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
                "change_transaction", "delete_transaction",
                "view_loan", "add_loan",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Ledger Customer", email="ledger@example.com",
            phone="01700000030", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer,
            account_number="LEDGER001",
            account_type="SAVINGS",
            balance=Decimal("1000.00"),
            status="ACTIVE",
        )

        self.account2 = BankAccount.objects.create(
            customer=customer,
            account_number="LEDGER002",
            account_type="SAVINGS",
            balance=Decimal("500.00"),
            status="ACTIVE",
        )

    def _system_wide_debit_credit_totals(self):

        from django.db.models import Sum

        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")

        for coa in ChartOfAccount.objects.all():

            d = coa.journal_lines.aggregate(s=Sum("debit"))["s"] or Decimal("0.00")
            c = coa.journal_lines.aggregate(s=Sum("credit"))["s"] or Decimal("0.00")

            total_debit += d
            total_credit += c

        return total_debit, total_credit

    def test_post_journal_entry_rejects_unbalanced_lines(self):

        with self.assertRaises(ValueError):

            ledger.post_journal_entry(
                lines=[
                    {"account_code": "1001", "debit": Decimal("100.00")},
                    {"account_code": "2001", "credit": Decimal("50.00")},
                ],
                description="Deliberately unbalanced",
            )

    def test_post_journal_entry_rejects_both_debit_and_credit_on_one_line(self):

        with self.assertRaises(ValueError):

            ledger.post_journal_entry(
                lines=[
                    {
                        "account_code": "1001",
                        "debit": Decimal("100.00"),
                        "credit": Decimal("100.00"),
                    },
                ],
                description="Invalid line",
            )

    def test_deposit_posts_balanced_journal_entry(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "DEPOSIT",
                "amount": "250.00",
            },
        )

        entry = JournalEntry.objects.latest("id")
        lines = entry.lines.all()

        self.assertEqual(lines.count(), 2)

        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal("250.00"))

    def test_transfer_posts_balanced_journal_entry(self):

        self.client.post(
            reverse("transaction_transfer"),
            {
                "account": self.account.id,
                "destination_account": self.account2.id,
                "transaction_type": "TRANSFER",
                "amount": "150.00",
            },
        )

        entry = JournalEntry.objects.latest("id")
        lines = entry.lines.all()

        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        self.assertEqual(total_debit, total_credit)

    def test_loan_disbursement_credits_balance_and_posts_ledger(self):

        response = self.client.post(
            reverse("loan_create"),
            {
                "account": self.account.id,
                "principal": "3000.00",
                "annual_rate": "10.00",
                "months": "6",
                "start_date": "2026-01-01",
            },
        )

        self.assertEqual(response.status_code, 302)

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("4000.00"))

        disbursement_txn = Transaction.objects.filter(
            transaction_type="LOAN_DISBURSEMENT",
        ).latest("id")

        self.assertEqual(disbursement_txn.amount, Decimal("3000.00"))

        entry = JournalEntry.objects.get(source_transaction=disbursement_txn)
        lines = entry.lines.all()

        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal("3000.00"))

    def test_reversing_transaction_mirrors_journal_entry_and_stays_balanced(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "DEPOSIT",
                "amount": "100.00",
            },
        )

        txn = Transaction.objects.filter(account=self.account).latest("id")
        original_entry_id = JournalEntry.objects.get(source_transaction=txn).id

        self.client.post(reverse("transaction_reverse", args=[txn.id]))

        # Original journal entry is kept, not deleted - full audit
        # trail per the roadmap's "never delete" principle.
        self.assertTrue(JournalEntry.objects.filter(id=original_entry_id).exists())

        # A new mirrored entry exists for the reversal itself.
        reversal_txn = Transaction.objects.get(reverses=txn)
        self.assertTrue(
            JournalEntry.objects.filter(source_transaction=reversal_txn).exists()
        )

        total_debit, total_credit = self._system_wide_debit_credit_totals()
        self.assertEqual(total_debit, total_credit)

    def test_books_stay_balanced_across_multiple_operations(self):

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "DEPOSIT", "amount": "500.00"},
        )
        self.client.post(
            reverse("transaction_create"),
            {"account": self.account2.id, "transaction_type": "WITHDRAW", "amount": "50.00"},
        )
        self.client.post(
            reverse("transaction_transfer"),
            {
                "account": self.account.id,
                "destination_account": self.account2.id,
                "transaction_type": "TRANSFER",
                "amount": "200.00",
            },
        )
        self.client.post(
            reverse("loan_create"),
            {
                "account": self.account.id,
                "principal": "1000.00",
                "annual_rate": "5.00",
                "months": "12",
                "start_date": "2026-01-01",
            },
        )

        from django.core.management import call_command
        call_command("credit_interest")

        total_debit, total_credit = self._system_wide_debit_credit_totals()

        self.assertEqual(total_debit, total_credit)
        self.assertGreater(total_debit, Decimal("0.00"))


class ReversalTests(TestCase):

    """
    Tests for Priority 3's non-destructive reversal, across
    each transaction type that supports it.
    """

    def setUp(self):

        from django.core.management import call_command
        call_command("seed_chart_of_accounts")

        self.user = make_staff_user(
            "reversaluser",
            permissions=[
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction", "change_transaction",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Reversal Customer", email="reversal@example.com",
            phone="01700000040", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer, account_number="REV0001",
            account_type="SAVINGS", balance=Decimal("1000.00"),
            status="ACTIVE",
        )

        self.account2 = BankAccount.objects.create(
            customer=customer, account_number="REV0002",
            account_type="SAVINGS", balance=Decimal("500.00"),
            status="ACTIVE",
        )

    def test_reversing_deposit_restores_balance(self):

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "DEPOSIT", "amount": "200.00"},
        )

        txn = Transaction.objects.filter(account=self.account).latest("id")

        self.client.post(reverse("transaction_reverse", args=[txn.id]))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_reversing_withdraw_restores_balance(self):

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "WITHDRAW", "amount": "150.00"},
        )

        txn = Transaction.objects.filter(account=self.account).latest("id")

        self.client.post(reverse("transaction_reverse", args=[txn.id]))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_reversing_transfer_restores_both_balances(self):

        self.client.post(
            reverse("transaction_transfer"),
            {
                "account": self.account.id,
                "destination_account": self.account2.id,
                "transaction_type": "TRANSFER",
                "amount": "300.00",
            },
        )

        txn = Transaction.objects.filter(
            account=self.account, transaction_type="TRANSFER",
        ).latest("id")

        self.client.post(reverse("transaction_reverse", args=[txn.id]))

        self.account.refresh_from_db()
        self.account2.refresh_from_db()

        self.assertEqual(self.account.balance, Decimal("1000.00"))
        self.assertEqual(self.account2.balance, Decimal("500.00"))

    def test_cannot_reverse_twice(self):

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "DEPOSIT", "amount": "50.00"},
        )

        txn = Transaction.objects.filter(account=self.account).latest("id")

        self.client.post(reverse("transaction_reverse", args=[txn.id]))

        # Second attempt should show the error, not double-reverse
        response = self.client.post(reverse("transaction_reverse", args=[txn.id]))

        self.account.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_original_transaction_row_is_never_deleted(self):

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "DEPOSIT", "amount": "75.00"},
        )

        txn = Transaction.objects.filter(account=self.account).latest("id")
        txn_id = txn.id

        self.client.post(reverse("transaction_reverse", args=[txn_id]))

        self.assertTrue(Transaction.objects.filter(id=txn_id).exists())


class FeeRuleTests(TestCase):

    def setUp(self):

        from django.core.management import call_command
        call_command("seed_chart_of_accounts")

        self.user = make_staff_user(
            "feeuser",
            permissions=[
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Fee Customer", email="fee@example.com",
            phone="01700000041", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer, account_number="FEE0001",
            account_type="SAVINGS", balance=Decimal("1000.00"),
            status="ACTIVE",
        )

    def test_inactive_fee_rule_does_not_charge(self):

        FeeRule.objects.create(
            name="Inactive Withdraw Fee", transaction_type="WITHDRAW",
            fee_type="FLAT", amount=Decimal("10.00"), is_active=False,
        )

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "WITHDRAW", "amount": "100.00"},
        )

        self.account.refresh_from_db()

        # Only the withdrawal itself, no fee since rule is inactive
        self.assertEqual(self.account.balance, Decimal("900.00"))

    def test_active_flat_fee_charged_on_withdraw(self):

        FeeRule.objects.create(
            name="Active Withdraw Fee", transaction_type="WITHDRAW",
            fee_type="FLAT", amount=Decimal("10.00"), is_active=True,
        )

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "WITHDRAW", "amount": "100.00"},
        )

        self.account.refresh_from_db()

        # 100 withdrawn + 10 fee = 110 total deducted
        self.assertEqual(self.account.balance, Decimal("890.00"))

        txn = Transaction.objects.filter(
            account=self.account, transaction_type="WITHDRAW",
        ).latest("id")
        self.assertEqual(txn.fee_amount, Decimal("10.00"))

    def test_active_percentage_fee_charged_on_withdraw(self):

        FeeRule.objects.create(
            name="Percentage Withdraw Fee", transaction_type="WITHDRAW",
            fee_type="PERCENTAGE", amount=Decimal("2.00"), is_active=True,
        )

        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "WITHDRAW", "amount": "100.00"},
        )

        self.account.refresh_from_db()

        # 100 withdrawn + 2% of 100 = 2.00 fee = 102 total deducted
        self.assertEqual(self.account.balance, Decimal("898.00"))


class LoanRepaymentTests(TestCase):

    """
    Tests for Priority 4 of the roadmap: real installment
    tracking, payment recording, and overdue detection.
    """

    def setUp(self):

        from django.core.management import call_command
        call_command("seed_chart_of_accounts")

        self.user = make_staff_user(
            "loanrepayuser",
            permissions=[
                "view_bankaccount", "add_bankaccount",
                "view_loan", "add_loan", "change_loan",
                "view_loaninstallment", "change_loaninstallment",
                "view_transaction", "add_transaction",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Loan Repay Customer", email="loanrepay@example.com",
            phone="01700000050", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer, account_number="LOANREPAY001",
            account_type="SAVINGS", balance=Decimal("0.00"),
            status="ACTIVE",
        )

    def _create_loan(self, principal="12000.00", rate="12.00", months="12"):

        self.client.post(
            reverse("loan_create"),
            {
                "account": self.account.id,
                "principal": principal,
                "annual_rate": rate,
                "months": months,
                "start_date": "2026-01-01",
            },
        )

        return Loan.objects.latest("id")

    def test_loan_creation_generates_installment_schedule(self):

        loan = self._create_loan(months="6")

        self.assertEqual(loan.installments.count(), 6)

        first = loan.installments.get(installment_no=1)
        self.assertEqual(first.status, "PENDING")

    def test_recording_payment_deducts_balance_and_marks_paid(self):

        loan = self._create_loan(principal="12000.00", months="12")

        self.account.refresh_from_db()
        balance_after_disbursement = self.account.balance

        installment = loan.installments.get(installment_no=1)
        expected_deduction = installment.total_due

        response = self.client.post(reverse("loan_repay", args=[loan.id]))

        self.assertEqual(response.status_code, 302)

        self.account.refresh_from_db()
        installment.refresh_from_db()

        self.assertEqual(
            self.account.balance,
            balance_after_disbursement - expected_deduction,
        )
        self.assertEqual(installment.status, "PAID")
        self.assertIsNotNone(installment.paid_date)

    def test_paying_all_installments_closes_loan(self):

        loan = self._create_loan(principal="1000.00", months="2")

        # Deposit enough to cover both installments
        self.client.post(
            reverse("transaction_create"),
            {"account": self.account.id, "transaction_type": "DEPOSIT", "amount": "2000.00"},
        )

        self.client.post(reverse("loan_repay", args=[loan.id]))
        self.client.post(reverse("loan_repay", args=[loan.id]))

        loan.refresh_from_db()
        self.assertEqual(loan.status, "CLOSED")
        self.assertEqual(
            loan.installments.filter(status="PAID").count(), 2,
        )

    def test_repayment_insufficient_balance_blocked(self):

        loan = self._create_loan(principal="50000.00", months="12")

        # Drain the account back to near-zero
        self.account.balance = Decimal("1.00")
        self.account.save(update_fields=["balance"])

        response = self.client.post(reverse("loan_repay", args=[loan.id]))

        self.assertEqual(response.status_code, 200)

        installment = loan.installments.get(installment_no=1)
        self.assertEqual(installment.status, "PENDING")

    def test_mark_overdue_installments_flags_and_penalizes(self):

        from django.core.management import call_command
        from datetime import date

        loan = self._create_loan(principal="6000.00", months="6")

        installment = loan.installments.get(installment_no=1)
        installment.due_date = date(2020, 1, 1)
        installment.save(update_fields=["due_date"])

        call_command("mark_overdue_installments")

        installment.refresh_from_db()

        self.assertEqual(installment.status, "OVERDUE")
        self.assertGreater(installment.penalty_amount, Decimal("0.00"))

    def test_mark_overdue_does_not_double_penalize(self):

        from django.core.management import call_command
        from datetime import date

        loan = self._create_loan(principal="6000.00", months="6")

        installment = loan.installments.get(installment_no=1)
        installment.due_date = date(2020, 1, 1)
        installment.save(update_fields=["due_date"])

        call_command("mark_overdue_installments")
        installment.refresh_from_db()
        penalty_after_first_run = installment.penalty_amount

        call_command("mark_overdue_installments")
        installment.refresh_from_db()

        self.assertEqual(installment.penalty_amount, penalty_after_first_run)

    def test_loan_repayment_posts_balanced_ledger_entry(self):

        loan = self._create_loan(principal="12000.00", months="12")

        self.client.post(reverse("loan_repay", args=[loan.id]))

        repayment_txn = Transaction.objects.filter(
            transaction_type="LOAN_REPAYMENT",
        ).latest("id")

        entry = JournalEntry.objects.get(source_transaction=repayment_txn)
        lines = entry.lines.all()

        total_debit = sum(l.debit for l in lines)
        total_credit = sum(l.credit for l in lines)

        self.assertEqual(total_debit, total_credit)


class PortalTests(TestCase):

    """
    Tests for Priority 5's customer self-service portal:
    data isolation between customers, staff-page blocking,
    and the enable/reset password management flow.
    """

    def setUp(self):

        from django.contrib.auth.models import User

        self.staff_user = make_staff_user(
            "portalstaff", permissions=["view_customer", "change_customer"],
        )

        self.staff_client = Client()
        self.staff_client.force_login(self.staff_user)

        self.customer_a = Customer.objects.create(
            name="Portal Customer A", email="porta@example.com",
            phone="01700000050", address="Dhaka",
        )

        self.customer_b = Customer.objects.create(
            name="Portal Customer B", email="portb@example.com",
            phone="01700000051", address="Dhaka",
        )

        self.account_a = BankAccount.objects.create(
            customer=self.customer_a, account_number="PORTA001",
            account_type="SAVINGS", balance=Decimal("500.00"),
            status="ACTIVE",
        )

        self.account_b = BankAccount.objects.create(
            customer=self.customer_b, account_number="PORTB001",
            account_type="SAVINGS", balance=Decimal("750.00"),
            status="ACTIVE",
        )

        self.user_a = User.objects.create_user(
            username="customer_a_login", password="testpass123", is_staff=False,
        )

        self.portal_a = CustomerPortalAccount.objects.create(
            user=self.user_a, customer=self.customer_a,
        )

        self.portal_client = Client()
        self.portal_client.force_login(self.user_a)

    def test_portal_customer_sees_own_dashboard_on_home(self):

        response = self.portal_client.get("/", follow=True)

        self.assertContains(response, "Portal Customer A")
        self.assertContains(response, "PORTA001")

    def test_portal_customer_does_not_see_other_customers_data(self):

        response = self.portal_client.get(reverse("portal_dashboard"))

        self.assertNotContains(response, "Portal Customer B")
        self.assertNotContains(response, "PORTB001")

    def test_portal_customer_cannot_access_other_customer_account_directly(self):

        response = self.portal_client.get(
            reverse("portal_account_detail", args=[self.account_b.id]),
        )

        self.assertEqual(response.status_code, 404)

    def test_portal_customer_blocked_from_staff_pages(self):

        for url_name in ["customer_list", "bank_account_list", "audit_log_list"]:

            response = self.portal_client.get(reverse(url_name))
            self.assertEqual(response.status_code, 403)

    def test_staff_user_without_portal_account_sees_staff_dashboard(self):

        response = self.staff_client.get("/", follow=True)

        self.assertContains(response, "BANKSYS DASHBOARD")

    def test_enable_portal_access_creates_account(self):

        response = self.staff_client.post(
            reverse("customer_portal_enable", args=[self.customer_b.id]),
            {"username": "customer_b_login"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            CustomerPortalAccount.objects.filter(customer=self.customer_b).exists()
        )

    def test_enable_portal_access_rejects_duplicate_username(self):

        response = self.staff_client.post(
            reverse("customer_portal_enable", args=[self.customer_b.id]),
            {"username": "customer_a_login"},
        )

        self.assertFalse(
            CustomerPortalAccount.objects.filter(customer=self.customer_b).exists()
        )

    def test_reset_password_changes_credentials(self):

        old_password_hash = self.user_a.password

        response = self.staff_client.post(
            reverse("customer_portal_reset_password", args=[self.customer_a.id]),
        )

        self.assertEqual(response.status_code, 200)

        self.user_a.refresh_from_db()
        self.assertNotEqual(self.user_a.password, old_password_hash)


class ApiHardeningTests(TestCase):

    """
    Tests for Priority 6: JWT auth, URL-path versioning, and
    filtering on the DRF API.
    """

    def setUp(self):

        self.user = make_staff_user(
            "apiuser", permissions=["view_customer", "view_bankaccount"],
        )
        self.user.set_password("apitestpass123")
        self.user.save()

        self.customer = Customer.objects.create(
            name="API Test Customer", email="apitest@example.com",
            phone="01700000060", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=self.customer, account_number="API0001",
            account_type="CURRENT", balance=Decimal("300.00"),
            status="ACTIVE",
        )

    def test_jwt_token_obtain_and_authenticated_request(self):

        import json

        client = Client()

        response = client.post(
            reverse("token_obtain_pair"),
            {"username": "apiuser", "password": "apitestpass123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        access = json.loads(response.content)["access"]

        response = client.get(
            "/api/v1/customers/", HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertEqual(response.status_code, 200)

    def test_unversioned_api_path_no_longer_exists(self):

        client = Client()
        client.force_login(self.user)

        response = client.get("/api/customers/")

        self.assertEqual(response.status_code, 404)

    def test_invalid_api_version_rejected(self):

        client = Client()
        client.force_login(self.user)

        response = client.get("/api/v2/customers/")

        self.assertEqual(response.status_code, 404)

    def test_filtering_by_account_type(self):

        import json

        BankAccount.objects.create(
            customer=self.customer, account_number="API0002",
            account_type="SAVINGS", balance=Decimal("100.00"),
            status="ACTIVE",
        )

        client = Client()
        client.force_login(self.user)

        response = client.get("/api/v1/accounts/?account_type=CURRENT")
        data = json.loads(response.content)

        self.assertTrue(
            all(r["account_type"] == "CURRENT" for r in data["results"])
        )

    def test_api_respects_branch_scoping(self):

        import json
        from accounts.models import Branch, EmployeeProfile
        from django.contrib.auth.models import Group

        other_branch = Branch.objects.create(name="Other Branch", code="OTH-01")

        other_customer = Customer.objects.create(
            name="Other Branch Customer", email="other@example.com",
            phone="01700000061", address="Dhaka", branch=other_branch,
        )

        manager_group, _ = Group.objects.get_or_create(name="B_MANAGER")
        perm = Permission.objects.get(
            content_type__app_label="accounts", codename="view_customer",
        )
        manager_group.permissions.add(perm)

        manager = User.objects.create_user(
            username="branchmanager2", password="testpass123", is_staff=True,
        )
        manager.groups.add(manager_group)
        EmployeeProfile.objects.create(
            user=manager, role="BRANCH_MANAGER", branch=other_branch,
        )

        client = Client()
        client.force_login(manager)

        response = client.get("/api/v1/customers/")
        data = json.loads(response.content) if response.content else {}

        names = [r["name"] for r in data.get("results", [])]

        self.assertIn("Other Branch Customer", names)
        self.assertNotIn("API Test Customer", names)


class ConcurrencyTests(TransactionTestCase):

    """
    Proves the select_for_update() row-locking used throughout
    the money-movement views actually prevents a double-spend
    under real concurrent requests, not just sequential ones.

    Uses TransactionTestCase (not TestCase) because this needs
    genuine cross-thread database visibility - TestCase wraps
    each test in an uncommitted transaction that other threads
    can't see into.

    Note: the dev/demo database is SQLite, which serializes
    writers at the connection level rather than providing true
    PostgreSQL-style row locks - but the net effect for this
    test is the same: two simultaneous withdrawals that would
    together overdraw the account must not both succeed. In
    production Postgres, select_for_update() provides the
    stronger per-row guarantee directly.
    """

    def setUp(self):

        self.user = make_staff_user(
            "concurrencyuser",
            permissions=[
                "view_customer", "add_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
            ],
        )

        customer = Customer.objects.create(
            name="Concurrency Customer", email="concurrency@example.com",
            phone="01700000070", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=customer, account_number="CONC0001",
            account_type="SAVINGS", balance=Decimal("100.00"),
            status="ACTIVE",
        )

    def test_two_simultaneous_withdrawals_cannot_both_succeed(self):

        import threading
        from django.db.utils import OperationalError

        barrier = threading.Barrier(2)
        results = []

        def attempt_withdraw():

            client = Client()
            client.force_login(self.user)

            # Synchronize both threads to submit as close to
            # simultaneously as possible.
            barrier.wait()

            try:

                response = client.post(
                    reverse("transaction_create"),
                    {
                        "account": self.account.id,
                        "transaction_type": "WITHDRAW",
                        "amount": "80.00",
                    },
                )

                results.append(response.status_code)

            except OperationalError:

                # SQLite serialized the second writer and it
                # timed out rather than gracefully queuing -
                # this still counts as "did not succeed".
                results.append("locked")

            finally:

                # Threads created directly (not via Django's
                # request-handling machinery) must close their
                # own DB connection explicitly - otherwise it
                # leaks. Harmless on SQLite (file-based, no
                # server-side session) but on a real backend
                # (Postgres/MySQL) a leaked connection can block
                # later operations like dropping the test
                # database during teardown.
                from django.db import connection
                connection.close()

        threads = [
            threading.Thread(target=attempt_withdraw) for _ in range(2)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        self.account.refresh_from_db()

        successful_withdrawals = Transaction.objects.filter(
            account=self.account, transaction_type="WITHDRAW",
        ).count()

        # The critical assertion: balance never went negative,
        # and at most one of the two ৳80 withdrawals actually
        # went through against a ৳100 starting balance.
        self.assertGreaterEqual(self.account.balance, Decimal("0.00"))
        self.assertLessEqual(successful_withdrawals, 1)

        if successful_withdrawals == 1:
            self.assertEqual(self.account.balance, Decimal("20.00"))
        else:
            self.assertEqual(self.account.balance, Decimal("100.00"))


class SecurityHardeningTests(TestCase):

    """
    Tests for Priority 7: CSRF enforcement and password policy,
    on top of the concurrency test above and the environment-
    driven production settings (SESSION_COOKIE_SECURE etc.),
    which are verified with `manage.py check --deploy` rather
    than a unit test since they're settings, not behavior.
    """

    def test_post_without_csrf_token_is_rejected(self):

        user = make_staff_user(
            "csrfuser", permissions=["view_customer", "add_customer"],
        )

        # enforce_csrf_checks=True makes this behave like a real
        # browser request instead of the test client's default
        # CSRF-exempt convenience mode.
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(user)

        response = strict_client.post(
            reverse("customer_create"),
            {
                "name": "Should Be Blocked",
                "email": "blocked@example.com",
                "phone": "01700000071",
                "address": "Dhaka",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Customer.objects.filter(name="Should Be Blocked").exists()
        )

    def test_weak_password_rejected_on_change(self):

        user = make_staff_user("weakpwuser")
        user.set_password("CorrectHorseBattery1")
        user.save()

        client = Client()
        client.force_login(user)

        response = client.post(
            reverse("password_change"),
            {
                "old_password": "CorrectHorseBattery1",
                "new_password1": "12345678",
                "new_password2": "12345678",
            },
        )

        # Form re-renders with a validation error rather than
        # succeeding - AUTH_PASSWORD_VALIDATORS (common password
        # + numeric-only validators) reject it.
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.check_password("CorrectHorseBattery1"))


class NotificationDeliveryTests(TestCase):

    """
    Verifies notify_transaction() is actually wired into the
    deposit flow and produces a correctly-addressed email.

    Note: Django's test runner always overrides the mail
    backend to an in-memory one during tests, so this can't
    catch mail *backend-configuration* bugs (a wrong MAILERS
    setting, a missing SMTP host, etc). Those need to be
    checked by exercising send_mail() directly, outside the
    test runner, against a real settings module.
    """

    def test_deposit_actually_queues_a_notification_email(self):

        from django.core import mail

        user = make_staff_user(
            "notifyuser",
            permissions=[
                "view_customer", "add_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
            ],
        )

        customer = Customer.objects.create(
            name="Notify Customer", email="notify@example.com",
            phone="01700000080", address="Dhaka",
        )

        account = BankAccount.objects.create(
            customer=customer, account_number="NOTIFY001",
            account_type="SAVINGS", balance=Decimal("100.00"),
            status="ACTIVE",
        )

        client = Client()
        client.force_login(user)

        self.assertEqual(len(mail.outbox), 0)

        client.post(
            reverse("transaction_create"),
            {
                "account": account.id,
                "transaction_type": "DEPOSIT",
                "amount": "50.00",
            },
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("notify@example.com", mail.outbox[0].to)
        self.assertIn("Deposit", mail.outbox[0].subject)


class EmailConfigurationTests(TestCase):

    """
    Regression guard for the real bug found during Priority 8
    testing: passing SMTP-only OPTIONS (host/port/username/etc)
    to the console backend raises InvalidMailer, since
    BaseEmailBackend rejects unrecognized keyword arguments when
    constructed via MAILERS. Confirms the mailer connection for
    the *current* settings (console backend, by default) can
    actually be constructed - this is exactly the call that
    would fail if a future change reintroduced mismatched
    OPTIONS for the selected backend.
    """

    def test_default_mailer_connection_constructs_without_error(self):

        from django.core.mail import mailers

        connection = mailers.default

        self.assertIsNotNone(connection)


class FraudDetectionTests(TestCase):

    """
    Tests for Priority 9: the rule-based fraud detection engine
    and staff review workflow. Flagging is purely additive -
    every test here also confirms the underlying transaction
    still succeeds normally, since a false positive should
    never block real banking activity.
    """

    def setUp(self):

        self.user = make_staff_user(
            "frauduser",
            permissions=[
                "view_customer", "add_customer",
                "view_bankaccount", "add_bankaccount",
                "view_transaction", "add_transaction",
                "view_fraudalert", "change_fraudalert",
            ],
        )

        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            name="Fraud Test Customer", email="fraudtest@example.com",
            phone="01700000090", address="Dhaka",
        )

        self.account = BankAccount.objects.create(
            customer=self.customer, account_number="FRAUD0001",
            account_type="SAVINGS", balance=Decimal("200000.00"),
            status="ACTIVE",
        )

    def test_large_transaction_creates_alert_and_still_succeeds(self):

        response = self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "WITHDRAW",
                "amount": "60000.00",
            },
        )

        self.assertEqual(response.status_code, 302)

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("140000.00"))

        alert = FraudAlert.objects.filter(
            transaction__account=self.account,
        ).first()

        self.assertIsNotNone(alert)
        self.assertGreaterEqual(alert.risk_score, 50)
        self.assertEqual(alert.status, "PENDING_REVIEW")

    def test_small_transaction_does_not_create_alert(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "DEPOSIT",
                "amount": "500.00",
            },
        )

        self.assertFalse(
            FraudAlert.objects.filter(
                transaction__account=self.account,
            ).exists()
        )

    def test_weak_signals_combine_to_cross_threshold(self):

        """
        A brand-new account (weight 35) plus 3 rapid transactions
        (velocity, weight 30) neither cross ALERT_THRESHOLD alone,
        but together (65) they do - proving the additive scoring
        actually combines signals rather than just checking rules
        independently.
        """

        fresh_customer = Customer.objects.create(
            name="Fresh Customer", email="freshtest@example.com",
            phone="01700000091", address="Dhaka",
        )

        fresh_account = BankAccount.objects.create(
            customer=fresh_customer, account_number="FRESH0001",
            account_type="SAVINGS", balance=Decimal("0.00"),
            status="ACTIVE",
        )

        # First deposit alone: new_account_large_transaction (35)
        # is below threshold - should not yet alert.
        self.client.post(
            reverse("transaction_create"),
            {
                "account": fresh_account.id,
                "transaction_type": "DEPOSIT",
                "amount": "25000.00",
            },
        )

        self.assertFalse(
            FraudAlert.objects.filter(
                transaction__account=fresh_account,
            ).exists()
        )

        # Two more rapid deposits bring velocity into play too.
        self.client.post(
            reverse("transaction_create"),
            {
                "account": fresh_account.id,
                "transaction_type": "DEPOSIT",
                "amount": "21000.00",
            },
        )
        self.client.post(
            reverse("transaction_create"),
            {
                "account": fresh_account.id,
                "transaction_type": "DEPOSIT",
                "amount": "22000.00",
            },
        )

        alert = FraudAlert.objects.filter(
            transaction__account=fresh_account,
        ).first()

        self.assertIsNotNone(alert)
        self.assertGreaterEqual(alert.risk_score, 50)

    def test_resolving_as_false_positive_does_not_block_account(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "WITHDRAW",
                "amount": "60000.00",
            },
        )

        alert = FraudAlert.objects.filter(
            transaction__account=self.account,
        ).first()

        self.client.post(
            reverse("fraud_alert_resolve", args=[alert.id]),
            {"status": "FALSE_POSITIVE"},
        )

        alert.refresh_from_db()
        self.account.refresh_from_db()

        self.assertEqual(alert.status, "FALSE_POSITIVE")
        self.assertEqual(alert.reviewed_by, self.user)
        self.assertEqual(self.account.status, "ACTIVE")

    def test_confirming_fraud_blocks_the_account(self):

        self.client.post(
            reverse("transaction_create"),
            {
                "account": self.account.id,
                "transaction_type": "WITHDRAW",
                "amount": "60000.00",
            },
        )

        alert = FraudAlert.objects.filter(
            transaction__account=self.account,
        ).first()

        self.client.post(
            reverse("fraud_alert_resolve", args=[alert.id]),
            {"status": "CONFIRMED_FRAUD"},
        )

        alert.refresh_from_db()
        self.account.refresh_from_db()

        self.assertEqual(alert.status, "CONFIRMED_FRAUD")
        self.assertEqual(self.account.status, "BLOCKED")

    def test_viewer_without_fraudalert_permission_blocked(self):

        viewer = make_staff_user(
            "fraudviewer_noaccess", groups=["B_VIEWER"],
        )

        client = Client()
        client.force_login(viewer)

        response = client.get(reverse("fraud_alert_list"))

        self.assertEqual(response.status_code, 403)


class LoginLockoutTests(TestCase):

    def test_account_locks_after_repeated_failed_logins(self):

        User.objects.create_user(
            username="locktest", password="correctpass123",
        )

        client = Client()

        for _ in range(5):

            client.post(
                reverse("login"),
                {"username": "locktest", "password": "wrongpass"},
            )

        response = client.post(
            reverse("login"),
            {"username": "locktest", "password": "correctpass123"},
        )

        # Locked out - axes returns 429, not a successful redirect
        self.assertEqual(response.status_code, 429)
