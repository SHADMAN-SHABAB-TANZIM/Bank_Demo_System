"""
Non-destructive transaction reversal.

Per the roadmap: never delete a financial transaction. To
correct a mistake, create a compensating REVERSAL transaction
that moves the money back, and mark the original as REVERSED -
the original row and its journal entry are never touched or
removed, preserving a complete audit trail.

This works generically across every transaction type
(DEPOSIT, WITHDRAW, TRANSFER, INTEREST, LOAN_DISBURSEMENT)
without type-specific logic:

1. Undo the original's balance effect on the affected
   account(s) by applying the exact opposite adjustment to
   their CURRENT balance (not by rewriting history - every
   other transaction's balance_after stays exactly as
   recorded, since nothing about them changes).
2. Record a new Transaction (type REVERSAL) documenting that
   movement, linked back via `reverses`.
3. Mirror the original's JournalEntry: a new entry with every
   line's debit/credit swapped. Since the original necessarily
   balanced (post_journal_entry enforces this at creation
   time), the mirrored entry is automatically balanced too.
"""

from decimal import Decimal

from django.db import transaction as db_transaction

from .ledger import post_journal_entry, generate_journal_reference
from .utils import generate_transaction_reference, log_action


class ReversalError(ValueError):
    pass


def reverse_transaction(original, user=None, request=None):

    from .models import BankAccount, Transaction

    if original.status == "REVERSED":
        raise ReversalError("This transaction has already been reversed.")

    if original.transaction_type == "REVERSAL":
        raise ReversalError("A reversal transaction cannot itself be reversed.")

    with db_transaction.atomic():

        account = (
            BankAccount.objects
            .select_for_update()
            .get(id=original.account_id)
        )

        destination = None

        if original.destination_account_id:

            destination = (
                BankAccount.objects
                .select_for_update()
                .get(id=original.destination_account_id)
            )

        # --------------------------------------------------------
        # Undo the balance effect. Every transaction type in this
        # app only ever adds or subtracts `amount` (and, for
        # transfers, also the opposite on destination) - so the
        # exact opposite adjustment always correctly reverses it,
        # regardless of type.
        # --------------------------------------------------------

        if original.transaction_type == "WITHDRAW":

            account.balance += original.amount

        elif original.transaction_type in (
            "DEPOSIT", "INTEREST", "LOAN_DISBURSEMENT",
        ):

            if account.balance < original.amount:

                raise ReversalError(
                    f"Cannot reverse: {account.account_number}'s current "
                    "balance is lower than the original amount."
                )

            account.balance -= original.amount

        elif original.transaction_type == "TRANSFER":

            if destination is None:

                raise ReversalError(
                    "Original transfer has no destination account on "
                    "record - cannot reverse."
                )

            if destination.balance < original.amount:

                raise ReversalError(
                    f"Cannot reverse: destination account "
                    f"{destination.account_number}'s current balance is "
                    "lower than the original amount."
                )

            account.balance += original.amount
            destination.balance -= original.amount

        else:

            raise ReversalError(
                f"Reversal is not supported for transaction type "
                f"'{original.transaction_type}'."
            )

        # Also undo any fee that was charged on the original -
        # fees are on the same account as `account`, so refund it.
        if original.fee_amount:
            account.balance += original.fee_amount

        account.save(update_fields=["balance"])

        if destination is not None:
            destination.save(update_fields=["balance"])

        # --------------------------------------------------------
        # Record the compensating transaction
        # --------------------------------------------------------

        reversal_txn = Transaction.objects.create(
            account=account,
            destination_account=destination,
            transaction_type="REVERSAL",
            amount=original.amount,
            balance_after=account.balance,
            reference=generate_transaction_reference(account),
            description=f"Reversal of {original.reference}",
            status="COMPLETED",
            reverses=original,
        )

        original.status = "REVERSED"
        original.save(update_fields=["status"])

        # --------------------------------------------------------
        # Mirror the original's journal entry (swap debit/credit
        # on every line) - guaranteed balanced since the original
        # was balanced when posted.
        # --------------------------------------------------------

        original_entries = original.journal_entries.prefetch_related("lines")

        for original_entry in original_entries:

            mirrored_lines = [
                {
                    "account_code": line.account.code,
                    "debit": line.credit,
                    "credit": line.debit,
                    "bank_account": line.bank_account,
                }
                for line in original_entry.lines.all()
            ]

            if mirrored_lines:

                post_journal_entry(
                    lines=mirrored_lines,
                    description=f"Reversal of {original_entry.reference}",
                    source_transaction=reversal_txn,
                    user=user,
                )

        log_action(
            request,
            "UPDATE",
            original,
            note=f"Reversed via {reversal_txn.reference}",
        )

        log_action(request, "CREATE", reversal_txn)

        return reversal_txn
