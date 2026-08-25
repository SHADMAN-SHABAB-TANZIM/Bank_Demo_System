from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


# Maps each role to (app_label, model, [action, ...]) tuples.
# Mirrors the GRANT statements on the Oracle roles:
#   b_viewer  - SELECT only, everywhere
#   b_teller  - + INSERT on accounts/transactions (day-to-day
#               counter work: open accounts, post transactions)
#   b_officer - + INSERT on customers (can onboard customers)
#   b_manager - + UPDATE on customers/accounts (can edit/manage)
#   b_admin   - full CRUD everywhere, plus the audit log and
#               standing orders / loans management

ROLES = {

    "B_VIEWER": {
        "customer": ["view"],
        "bankaccount": ["view"],
        "transaction": ["view"],
        "standingorder": ["view"],
        "loan": ["view"],
        "branch": ["view"],
    },

    "B_TELLER": {
        "customer": ["view"],
        "bankaccount": ["view", "add"],
        "transaction": ["view", "add"],
        "standingorder": ["view"],
        "loan": ["view"],
        "branch": ["view"],
    },

    "B_OFFICER": {
        "customer": ["view", "add"],
        "bankaccount": ["view", "add"],
        "transaction": ["view", "add"],
        "standingorder": ["view", "add"],
        "loan": ["view", "add"],
        "branch": ["view"],
    },

    "B_MANAGER": {
        "customer": ["view", "add", "change"],
        "bankaccount": ["view", "add", "change"],
        "transaction": ["view", "add", "change"],
        "standingorder": ["view", "add", "change"],
        "loan": ["view", "add", "change"],
        "branch": ["view"],
        "employeeprofile": ["view"],
    },

    "B_ADMIN": {
        "customer": ["view", "add", "change", "delete"],
        "bankaccount": ["view", "add", "change", "delete"],
        "transaction": ["view", "add", "change", "delete"],
        "standingorder": ["view", "add", "change", "delete"],
        "loan": ["view", "add", "change", "delete"],
        "auditlog": ["view"],
        "dailysnapshot": ["view"],
        "branch": ["view", "add", "change", "delete"],
        "employeeprofile": ["view", "add", "change", "delete"],
    },

}


class Command(BaseCommand):

    """
    Creates (or updates) five Django Groups mirroring the
    B_VIEWER / B_TELLER / B_OFFICER / B_MANAGER / B_ADMIN
    Oracle roles from the original schema, and assigns each
    the matching Django model permissions. Idempotent - safe
    to re-run any time new models are added.

    Usage:

        python manage.py setup_roles
    """

    help = "Create/update the B_VIEWER/B_TELLER/B_OFFICER/B_MANAGER/B_ADMIN groups."

    def handle(self, *args, **options):

        for role_name, model_actions in ROLES.items():

            group, created = Group.objects.get_or_create(name=role_name)

            perms = []

            for model_name, actions in model_actions.items():

                for action in actions:

                    codename = f"{action}_{model_name}"

                    try:

                        perm = Permission.objects.get(
                            content_type__app_label="accounts",
                            codename=codename,
                        )

                        perms.append(perm)

                    except Permission.DoesNotExist:

                        self.stdout.write(
                            self.style.WARNING(
                                f"  permission {codename} not found "
                                f"(skipped for {role_name} - run "
                                f"migrate first if this is unexpected)"
                            )
                        )

            group.permissions.set(perms)

            status = "created" if created else "updated"

            self.stdout.write(
                self.style.SUCCESS(
                    f"{status}: {role_name} ({len(perms)} permissions)"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Done. Assign staff users to a group in /admin/ "
                "(User -> Groups) to grant them that role."
            )
        )
