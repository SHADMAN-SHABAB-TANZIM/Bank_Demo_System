"""
Branch-scoping helpers.

A logged-in user is "branch-scoped" if they have an
EmployeeProfile whose role is not in
EmployeeProfile.UNSCOPED_ROLES (SUPER_ADMIN, SYSTEM_ADMIN,
AUDITOR). Scoped users only see customers/accounts/
transactions/loans belonging to their own branch; unscoped
users and any user without an EmployeeProfile at all (e.g.
existing users created before this feature, or the original
superuser) see everything - this keeps the feature additive
and non-breaking for accounts that predate it.
"""


def get_employee_profile(user):

    """
    Returns the user's EmployeeProfile, or None if they don't
    have one (e.g. a superuser created before branches existed).
    """

    return getattr(user, "employee_profile", None)


def get_user_branch(user):

    """
    Returns the Branch the user is scoped to, or None if they
    are unscoped (see everything) or have no EmployeeProfile.
    """

    profile = get_employee_profile(user)

    if profile is None:
        return None

    if not profile.is_branch_scoped:
        return None

    return profile.branch


def scope_to_branch(queryset, user, branch_field="branch"):

    """
    Filters `queryset` down to the user's branch if they're
    branch-scoped; returns it unchanged otherwise (unscoped
    role, superuser, or no EmployeeProfile at all).

    `branch_field` is the lookup path to a Branch FK on the
    queryset's model - e.g. "branch" for Customer/BankAccount,
    "account__branch" for Transaction/Loan/StandingOrder.
    """

    if user.is_superuser:
        return queryset

    branch = get_user_branch(user)

    if branch is None:
        return queryset

    return queryset.filter(**{branch_field: branch})
