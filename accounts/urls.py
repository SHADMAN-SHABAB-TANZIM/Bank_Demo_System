from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from . import portal_views


urlpatterns = [

    path(
        '',
        views.home,
        name='home'
    ),

    path(
        'customers/',
        views.customer_list,
        name='customer_list'
    ),

    path(
        'customers/create/',
        views.customer_create,
        name='customer_create'
    ),

    path(
        'customers/<int:customer_id>/',
        views.customer_detail,
        name='customer_detail'
    ),

    path(
        'customers/<int:customer_id>/update/',
        views.customer_update,
        name='customer_update'
    ),

    path(
        'customers/<int:customer_id>/delete/',
        views.customer_delete,
        name='customer_delete'
    ),

    path(
        'customers/<int:customer_id>/deactivate/',
        views.customer_deactivate,
        name='customer_deactivate'
    ),

    path(
        'customers/<int:customer_id>/reactivate/',
        views.customer_reactivate,
        name='customer_reactivate'
    ),

    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='accounts/login.html'
        ),
        name='login'
    ),

    path(
        'logout/',
        auth_views.LogoutView.as_view(),
        name='logout'
    ),

    path(
        'password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='accounts/password_change.html',
            success_url='/password-change/done/',
        ),
        name='password_change'
    ),

    path(
        'password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='accounts/password_change_done.html',
        ),
        name='password_change_done'
    ),

    # Bank Accounts

    path(
        'bank-accounts/',
        views.bank_account_list,
        name='bank_account_list'
    ),

    path(
        'bank-accounts/create/',
        views.bank_account_create,
        name='bank_account_create'
    ),

    path(
        'bank-accounts/<int:account_id>/',
        views.bank_account_detail,
        name='bank_account_detail'
    ),

    path(
        'bank-accounts/<int:account_id>/statement.csv',
        views.bank_account_statement_csv,
        name='bank_account_statement_csv'
    ),
    
    path(
        'bank-accounts/<int:account_id>/update/',
        views.bank_account_update,
        name='bank_account_update'
    ),

    path(
        'bank-accounts/<int:account_id>/delete/',
        views.bank_account_delete,
        name='bank_account_delete'
    ),
    path(
    'transactions/',
    views.transaction_list,
    name='transaction_list'
),
path(
    'transactions/export.csv',
    views.transaction_export_csv,
    name='transaction_export_csv'
),
path(
    'transactions/<int:transaction_id>/',
    views.transaction_detail,
    name='transaction_detail'
),
path(
    'transactions/create/',
    views.transaction_create,
    name='transaction_create'
),
path(
    'transactions/transfer/',
    views.transaction_transfer,
    name='transaction_transfer'
),
path(
    'transactions/<int:transaction_id>/update/',
    views.transaction_update,
    name='transaction_update'
),
path(
    'transactions/<int:transaction_id>/reverse/',
    views.transaction_reverse,
    name='transaction_reverse'
),

# Standing Orders

path(
    'standing-orders/',
    views.standing_order_list,
    name='standing_order_list'
),
path(
    'standing-orders/create/',
    views.standing_order_create,
    name='standing_order_create'
),
path(
    'standing-orders/<int:order_id>/update/',
    views.standing_order_update,
    name='standing_order_update'
),
path(
    'standing-orders/<int:order_id>/delete/',
    views.standing_order_delete,
    name='standing_order_delete'
),
path(
    'standing-orders/<int:order_id>/toggle/',
    views.standing_order_toggle,
    name='standing_order_toggle'
),

# Audit Log

path(
    'audit-log/',
    views.audit_log_list,
    name='audit_log_list'
),

# Loans

path(
    'loans/',
    views.loan_list,
    name='loan_list'
),
path(
    'loans/create/',
    views.loan_create,
    name='loan_create'
),
path(
    'loans/<int:loan_id>/',
    views.loan_detail,
    name='loan_detail'
),
path(
    'loans/<int:loan_id>/close/',
    views.loan_close,
    name='loan_close'
),
path(
    'loans/<int:loan_id>/repay/',
    views.loan_repay,
    name='loan_repay'
),
path(
    'emi-calculator/',
    views.emi_calculator,
    name='emi_calculator'
),

# Branches

path(
    'branches/',
    views.branch_list,
    name='branch_list'
),
path(
    'branches/create/',
    views.branch_create,
    name='branch_create'
),
path(
    'branches/<int:branch_id>/update/',
    views.branch_update,
    name='branch_update'
),
path(
    'branches/<int:branch_id>/delete/',
    views.branch_delete,
    name='branch_delete'
),

# Employee role assignment

path(
    'employees/',
    views.employee_list,
    name='employee_list'
),
path(
    'employees/assign/',
    views.employee_assign,
    name='employee_assign'
),
path(
    'employees/<int:profile_id>/update/',
    views.employee_update,
    name='employee_update'
),

# Ledger

path(
    'ledger/',
    views.trial_balance,
    name='trial_balance'
),
path(
    'ledger/<int:coa_id>/',
    views.ledger_account_detail,
    name='ledger_account_detail'
),

# Fee Rules

path(
    'fee-rules/',
    views.fee_rule_list,
    name='fee_rule_list'
),
path(
    'fee-rules/create/',
    views.fee_rule_create,
    name='fee_rule_create'
),
path(
    'fee-rules/<int:fee_rule_id>/update/',
    views.fee_rule_update,
    name='fee_rule_update'
),

# Customer Portal Access (staff-managed)

path(
    'customers/<int:customer_id>/portal/enable/',
    views.customer_portal_enable,
    name='customer_portal_enable'
),
path(
    'customers/<int:customer_id>/portal/reset-password/',
    views.customer_portal_reset_password,
    name='customer_portal_reset_password'
),

# Customer Self-Service Portal

path(
    'portal/',
    portal_views.portal_dashboard,
    name='portal_dashboard'
),
path(
    'portal/accounts/<int:account_id>/',
    portal_views.portal_account_detail,
    name='portal_account_detail'
),
path(
    'portal/transactions/',
    portal_views.portal_transaction_list,
    name='portal_transaction_list'
),
path(
    'portal/loans/',
    portal_views.portal_loan_list,
    name='portal_loan_list'
),
path(
    'portal/loans/<int:loan_id>/',
    portal_views.portal_loan_detail,
    name='portal_loan_detail'
),

# Fraud Alerts

path(
    'fraud-alerts/',
    views.fraud_alert_list,
    name='fraud_alert_list'
),
path(
    'fraud-alerts/<int:alert_id>/',
    views.fraud_alert_detail,
    name='fraud_alert_detail'
),
path(
    'fraud-alerts/<int:alert_id>/resolve/',
    views.fraud_alert_resolve,
    name='fraud_alert_resolve'
),
]