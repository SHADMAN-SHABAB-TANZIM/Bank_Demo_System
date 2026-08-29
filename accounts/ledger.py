"""
Double-entry ledger posting.

Every financial movement in the app (deposit, withdraw,
transfer, interest, loan disbursement) should post a balanced
JournalEntry alongside the customer-facing Transaction record.
This module is the single place that enforces "debits must
equal credits" and knows which Chart-of-Accounts codes each
movement type touches.

Design choice: posting is done via EXPLICIT calls from each
view/command right where the Transaction is created - not via
a post_save signal. This keeps the money-movement code and its
accounting consequences visible together at the call site
(same pattern already used for log_action and messages.success
elsewhere in this app), rather than hiding it behind implicit
signal magic that's harder to trace and test.
"""

from decimal import Decimal
from uuid import uuid4


# Chart of Accounts codes used by the posting functions below.
# Kept as constants so a typo becomes an ImportError/NameError
# at call sites instead of a silent wrong-account posting.
CASH = "1001"
LOANS_RECEIVABLE = "1101"
DEPOSITS_SAVINGS = "2001"
DEPOSITS_CURRENT = "2002"
INTEREST_INCOME = "4001"
FEE_INCOME = "4002"
INTEREST_EXPENSE = "5001"


def _deposit_liability_code(bank_account):

    """
    Which Chart-of-Accounts liability code represents a given
    customer BankAccount's balance, based on its account_type.
    """

    if bank_account.account_type == "SAVINGS":
        return DEPOSITS_SAVINGS

    return DEPOSITS_CURRENT


def generate_journal_reference():

    while True:

        from .models import JournalEntry

        reference = f"JE-{uuid4().hex[:10].upper()}"

        if not JournalEntry.objects.filter(reference=reference).exists():
            return reference


def post_journal_entry(lines, description="", source_transaction=None, user=None):

    """
    Creates one JournalEntry with the given lines. `lines` is
    a list of dicts, each either:

        {"account_code": "1001", "debit": Decimal("100.00"), "bank_account": None}
        {"account_code": "2001", "credit": Decimal("100.00"), "bank_account": some_account}

    Raises ValueError if total debits != total credits, or if
    any line has both/neither debit and credit set - this is
    the actual "books must balance" enforcement. Meant to be
    called inside the same db_transaction.atomic() block as
    the Transaction/balance update it accompanies, so a
    validation failure here rolls back the whole operation.
    """

    from .models import ChartOfAccount, JournalEntry, JournalLine

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for line in lines:

        debit = line.get("debit") or Decimal("0.00")
        credit = line.get("credit") or Decimal("0.00")

        if (debit > 0) == (credit > 0):

            raise ValueError(
                f"Journal line for account {line['account_code']} must "
                "have exactly one of debit/credit set, not both or "
                "neither."
            )

        total_debit += debit
        total_credit += credit

    if total_debit != total_credit:

        raise ValueError(
            f"Unbalanced journal entry: total debit {total_debit} != "
            f"total credit {total_credit}."
        )

    entry = JournalEntry.objects.create(
        reference=generate_journal_reference(),
        description=description,
        source_transaction=source_transaction,
        created_by=user,
    )

    coa_cache = {}

    for line in lines:

        code = line["account_code"]

        if code not in coa_cache:
            coa_cache[code] = ChartOfAccount.objects.get(code=code)

        JournalLine.objects.create(
            journal_entry=entry,
            account=coa_cache[code],
            bank_account=line.get("bank_account"),
            debit=line.get("debit") or Decimal("0.00"),
            credit=line.get("credit") or Decimal("0.00"),
        )

    return entry


def post_deposit(transaction, user=None):

    account = transaction.account

    return post_journal_entry(
        lines=[
            {"account_code": CASH, "debit": transaction.amount},
            {
                "account_code": _deposit_liability_code(account),
                "credit": transaction.amount,
                "bank_account": account,
            },
        ],
        description=f"Deposit to {account.account_number}",
        source_transaction=transaction,
        user=user,
    )


def post_withdraw(transaction, user=None):

    account = transaction.account

    return post_journal_entry(
        lines=[
            {
                "account_code": _deposit_liability_code(account),
                "debit": transaction.amount,
                "bank_account": account,
            },
            {"account_code": CASH, "credit": transaction.amount},
        ],
        description=f"Withdrawal from {account.account_number}",
        source_transaction=transaction,
        user=user,
    )


def post_transfer(transaction, user=None):

    source = transaction.account
    destination = transaction.destination_account

    return post_journal_entry(
        lines=[
            {
                "account_code": _deposit_liability_code(source),
                "debit": transaction.amount,
                "bank_account": source,
            },
            {
                "account_code": _deposit_liability_code(destination),
                "credit": transaction.amount,
                "bank_account": destination,
            },
        ],
        description=(
            f"Transfer {source.account_number} -> "
            f"{destination.account_number}"
        ),
        source_transaction=transaction,
        user=user,
    )


def post_interest(transaction, user=None):

    account = transaction.account

    return post_journal_entry(
        lines=[
            {"account_code": INTEREST_EXPENSE, "debit": transaction.amount},
            {
                "account_code": _deposit_liability_code(account),
                "credit": transaction.amount,
                "bank_account": account,
            },
        ],
        description=f"Interest credited to {account.account_number}",
        source_transaction=transaction,
        user=user,
    )


def post_fee(transaction, fee_amount, user=None):

    """
    Posts a separate journal entry for the fee portion of a
    transaction (kept separate from post_withdraw/post_transfer
    so those functions' signatures don't need to change for
    callers that never pass a fee - credit_interest,
    run_standing_orders, loan disbursement). Both entries share
    the same source_transaction, so the account's full ledger
    trail shows the base movement and the fee as distinct,
    individually-balanced lines.
    """

    account = transaction.account

    return post_journal_entry(
        lines=[
            {
                "account_code": _deposit_liability_code(account),
                "debit": fee_amount,
                "bank_account": account,
            },
            {"account_code": FEE_INCOME, "credit": fee_amount},
        ],
        description=f"Fee on {transaction.reference}",
        source_transaction=transaction,
        user=user,
    )


def post_loan_repayment(
    transaction, principal_portion, interest_portion, penalty_portion, user=None,
):

    """
    A loan repayment splits three ways:

    - principal_portion reduces the bank's Loans Receivable
      asset (the customer owes less now).
    - interest_portion is the bank's earnings - Interest
      Income.
    - penalty_portion (late fee, if any) also counts as
      income - posted to Fee Income.

    The customer's deposit liability is debited for the full
    amount paid (money leaving their account).
    """

    account = transaction.account

    total = principal_portion + interest_portion + penalty_portion

    lines = [
        {
            "account_code": _deposit_liability_code(account),
            "debit": total,
            "bank_account": account,
        },
    ]

    if principal_portion:
        lines.append({"account_code": LOANS_RECEIVABLE, "credit": principal_portion})

    if interest_portion:
        lines.append({"account_code": INTEREST_INCOME, "credit": interest_portion})

    if penalty_portion:
        lines.append({"account_code": FEE_INCOME, "credit": penalty_portion})

    return post_journal_entry(
        lines=lines,
        description=f"Loan repayment - {transaction.reference}",
        source_transaction=transaction,
        user=user,
    )


def post_loan_disbursement(loan, transaction=None, user=None):

    """
    Loan proceeds are credited to the borrower's account, so
    the bank's Loans Receivable asset increases while the
    customer's deposit liability increases too (money now
    sitting in their account, owed back via repayments).
    """

    account = loan.account

    return post_journal_entry(
        lines=[
            {"account_code": LOANS_RECEIVABLE, "debit": loan.principal},
            {
                "account_code": _deposit_liability_code(account),
                "credit": loan.principal,
                "bank_account": account,
            },
        ],
        description=f"Loan #{loan.id} disbursed to {account.account_number}",
        source_transaction=transaction,
        user=user,
    )
