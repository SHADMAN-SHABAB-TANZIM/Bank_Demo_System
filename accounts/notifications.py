"""
Best-effort email notifications for account activity.

Design principle: a notification failure (bad email address,
SMTP outage, misconfigured mailer) must NEVER roll back or
block the underlying financial transaction. Every function
here catches its own exceptions and logs them rather than
propagating - money movement always succeeds or fails on its
own merits, independent of whether the customer could be
notified about it.
"""

import logging

from django.core.mail import send_mail

logger = logging.getLogger(__name__)


TRANSACTION_TYPE_VERBS = {
    "DEPOSIT": "deposited into",
    "WITHDRAW": "withdrawn from",
    "TRANSFER": "transferred from",
    "INTEREST": "credited as interest to",
    "LOAN_DISBURSEMENT": "disbursed to",
    "LOAN_REPAYMENT": "paid from",
    "REVERSAL": "reversed on",
}


def _send(subject, message, to_email):

    if not to_email:
        return

    try:

        send_mail(
            subject=subject,
            message=message,
            from_email=None,  # uses DEFAULT_FROM_EMAIL
            recipient_list=[to_email],
            fail_silently=False,
        )

    except Exception:

        logger.exception(
            "Failed to send notification email to %s (subject: %s)",
            to_email, subject,
        )


def notify_transaction(transaction):

    """
    Notifies the account holder about a completed transaction.
    For transfers, also notifies the destination account's
    holder (a separate email, since it's a different customer
    unless they're transferring between their own accounts).
    """

    account = transaction.account
    customer = account.customer

    verb = TRANSACTION_TYPE_VERBS.get(
        transaction.transaction_type, "posted to",
    )

    subject = (
        f"BANKSYS: ৳{transaction.amount} {transaction.get_transaction_type_display()} "
        f"on {account.account_number}"
    )

    lines = [
        f"Hello {customer.name},",
        "",
        f"৳{transaction.amount} was {verb} your account "
        f"{account.account_number}.",
    ]

    if transaction.fee_amount:
        lines.append(f"A fee of ৳{transaction.fee_amount} was also charged.")

    lines += [
        f"New balance: ৳{account.balance}",
        f"Reference: {transaction.reference}",
        "",
        "If you did not expect this activity, please contact your branch.",
    ]

    _send(subject, "\n".join(lines), customer.email)

    if (
        transaction.transaction_type == "TRANSFER"
        and transaction.destination_account_id
    ):

        dest_account = transaction.destination_account
        dest_customer = dest_account.customer

        dest_subject = (
            f"BANKSYS: ৳{transaction.amount} received on "
            f"{dest_account.account_number}"
        )

        dest_lines = [
            f"Hello {dest_customer.name},",
            "",
            f"৳{transaction.amount} was credited to your account "
            f"{dest_account.account_number} via transfer.",
            f"New balance: ৳{dest_account.balance}",
            f"Reference: {transaction.reference}",
        ]

        _send(dest_subject, "\n".join(dest_lines), dest_customer.email)


def notify_portal_account_created(customer, username):

    """
    Deliberately does NOT include the password - passwords are
    shown once on-screen to the staff member creating the
    account, to be communicated through a separate secure
    channel, never by plain email.
    """

    subject = "BANKSYS: Your online banking access is ready"

    message = "\n".join([
        f"Hello {customer.name},",
        "",
        "Online access to your BANKSYS account has been set up.",
        f"Username: {username}",
        "",
        "Please contact your branch if you did not request this.",
    ])

    _send(subject, message, customer.email)
