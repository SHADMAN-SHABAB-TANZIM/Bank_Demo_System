from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    AuditLog,
    BankAccount,
    Branch,
    Customer,
    DailySnapshot,
    EmployeeProfile,
    Loan,
    StandingOrder,
    Transaction,
)
from .utils import amortization_schedule, calculate_emi


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

    def test_deleting_latest_transaction_reverses_balance(self):

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
            reverse("transaction_delete", args=[txn.id]),
        )

        self.account.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account.balance, Decimal("1000.00"))
        self.assertFalse(
            Transaction.objects.filter(id=txn.id).exists()
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
