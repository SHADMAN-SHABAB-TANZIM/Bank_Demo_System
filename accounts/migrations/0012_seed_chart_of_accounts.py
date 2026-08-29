from django.db import migrations


ACCOUNTS = [
    ("1001", "Cash and Cash Equivalents", "ASSET"),
    ("1101", "Loans Receivable", "ASSET"),
    ("2001", "Customer Deposits - Savings", "LIABILITY"),
    ("2002", "Customer Deposits - Current", "LIABILITY"),
    ("4001", "Interest Income", "INCOME"),
    ("4002", "Fee Income", "INCOME"),
    ("5001", "Interest Expense", "EXPENSE"),
]


def seed_chart_of_accounts(apps, schema_editor):

    ChartOfAccount = apps.get_model("accounts", "ChartOfAccount")

    for code, name, account_type in ACCOUNTS:

        ChartOfAccount.objects.get_or_create(
            code=code,
            defaults={"name": name, "account_type": account_type},
        )


def remove_chart_of_accounts(apps, schema_editor):

    ChartOfAccount = apps.get_model("accounts", "ChartOfAccount")

    ChartOfAccount.objects.filter(
        code__in=[code for code, _, _ in ACCOUNTS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_alter_transaction_transaction_type"),
    ]

    operations = [
        migrations.RunPython(
            seed_chart_of_accounts,
            remove_chart_of_accounts,
        ),
    ]
