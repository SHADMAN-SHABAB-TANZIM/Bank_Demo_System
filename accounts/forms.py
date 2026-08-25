from decimal import Decimal

from django import forms

from .models import (
    Customer,
    BankAccount,
    Transaction,
    StandingOrder,
    Loan,
    Branch,
    EmployeeProfile,
)


class CustomerForm(forms.ModelForm):

    class Meta:
        model = Customer

        fields = [
            'branch',
            'name',
            'email',
            'phone',
            'nid',
            'address',
        ]


class BankAccountForm(forms.ModelForm):

    class Meta:
        model = BankAccount

        fields = [
            'customer',
            'branch',
            'account_number',
            'account_type',
            'balance',
            'status',
        ]

        help_texts = {
            'branch': "Leave blank to use the customer's branch.",
        }

        widgets = {

            'balance': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0',
                    'placeholder': '0.00'
                }
            ),

        }


class TransactionForm(forms.ModelForm):

    class Meta:
        model = Transaction

        fields = [
            'account',
            'destination_account',
            'transaction_type',
            'amount',
            'description',
        ]

        widgets = {

            'amount': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0.01',
                    'placeholder': '0.00'
                }
            ),

            'description': forms.TextInput(
                attrs={
                    'placeholder': 'Transaction description'
                }
            ),

        }

    def clean_amount(self):

        amount = self.cleaned_data.get('amount')

        if amount is None:
            raise forms.ValidationError(
                'Amount is required.'
            )

        if amount <= Decimal('0.00'):
            raise forms.ValidationError(
                'Amount must be greater than zero.'
            )

        return amount

    def clean(self):

        cleaned_data = super().clean()

        transaction_type = cleaned_data.get(
            'transaction_type'
        )

        account = cleaned_data.get(
            'account'
        )

        destination_account = cleaned_data.get(
            'destination_account'
        )

        # Transfer validation
        if transaction_type == 'TRANSFER':

            if not destination_account:

                self.add_error(
                    'destination_account',
                    'Destination account is required for transfers.'
                )

            elif account == destination_account:

                self.add_error(
                    'destination_account',
                    'Source and destination accounts cannot be the same.'
                )

        return cleaned_data
    

class StandingOrderForm(forms.ModelForm):

    class Meta:
        model = StandingOrder

        fields = [
            'account',
            'destination_account',
            'amount',
            'frequency',
            'next_run_date',
            'description',
            'is_active',
        ]

        widgets = {

            'amount': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0.01',
                    'placeholder': '0.00'
                }
            ),

            'next_run_date': forms.DateInput(
                attrs={'type': 'date'}
            ),

            'description': forms.TextInput(
                attrs={
                    'placeholder': 'e.g. Monthly rent transfer'
                }
            ),

        }

    def clean(self):

        cleaned_data = super().clean()

        account = cleaned_data.get('account')
        destination_account = cleaned_data.get('destination_account')

        if account and destination_account and account == destination_account:

            self.add_error(
                'destination_account',
                'Source and destination accounts cannot be the same.'
            )

        return cleaned_data


class LoanForm(forms.ModelForm):

    class Meta:
        model = Loan

        fields = [
            'account',
            'principal',
            'annual_rate',
            'months',
            'start_date',
        ]

        widgets = {

            'principal': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '1',
                    'placeholder': '0.00'
                }
            ),

            'annual_rate': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0',
                    'placeholder': 'e.g. 12.00'
                }
            ),

            'months': forms.NumberInput(
                attrs={
                    'min': '1',
                    'placeholder': 'e.g. 12'
                }
            ),

            'start_date': forms.DateInput(
                attrs={'type': 'date'}
            ),

        }


class BranchForm(forms.ModelForm):

    class Meta:
        model = Branch

        fields = [
            'name',
            'code',
            'address',
            'phone',
            'is_active',
        ]

        widgets = {

            'code': forms.TextInput(
                attrs={'placeholder': 'e.g. DHK-01'}
            ),

        }


class EmployeeProfileForm(forms.ModelForm):

    """
    Used from the staff assignment page to set an existing
    Django User's role and branch. The `user` field is shown
    so a Super Admin/System Admin can pick which user to
    assign - it's excluded when editing an existing profile
    (the assignment view swaps it out for a read-only label
    instead, since a profile's user shouldn't change after
    creation).
    """

    class Meta:
        model = EmployeeProfile

        fields = [
            'user',
            'role',
            'branch',
            'employee_id',
        ]

    def clean(self):

        cleaned_data = super().clean()

        role = cleaned_data.get('role')
        branch = cleaned_data.get('branch')

        if role and branch and role in EmployeeProfile.UNSCOPED_ROLES:

            self.add_error(
                'branch',
                (
                    f"{dict(EmployeeProfile.ROLE_CHOICES).get(role)} "
                    "is not tied to a single branch - leave this blank."
                ),
            )

        if role and not branch and role not in EmployeeProfile.UNSCOPED_ROLES:

            self.add_error(
                'branch',
                "This role requires a branch assignment.",
            )

        return cleaned_data
