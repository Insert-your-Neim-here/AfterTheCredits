# users/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from movies.models import StreamingPlatform

User = get_user_model()


class SignupForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'Email address', 'autocomplete': 'email'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Password', 'autocomplete': 'new-password'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm password', 'autocomplete': 'new-password'})
    )
    streaming_platforms = forms.ModelMultipleChoiceField(
        queryset=StreamingPlatform.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['streaming_platforms'].queryset = StreamingPlatform.objects.order_by('name')

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('password')
        cpw = cleaned.get('confirm_password')
        if pw and cpw and pw != cpw:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'Email address', 'autocomplete': 'email'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Password', 'autocomplete': 'current-password'})
    )


class ForgotPasswordEmailForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'Email address', 'autocomplete': 'email'})
    )

    def clean_email(self):
        return self.cleaned_data['email'].lower()


class VerificationCodeForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'placeholder': '6-digit code',
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
        })
    )

    def clean_code(self):
        return self.cleaned_data['code'].strip()


class PasswordResetConfirmForm(forms.Form):
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'New password', 'autocomplete': 'new-password'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm new password', 'autocomplete': 'new-password'})
    )

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('new_password')
        confirm_password = cleaned.get('confirm_password')
        if password and confirm_password and password != confirm_password:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned



class ProfileForm(forms.ModelForm):
    streaming_platforms = forms.ModelMultipleChoiceField(
        queryset=StreamingPlatform.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Your streaming platforms",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['streaming_platforms'].queryset = StreamingPlatform.objects.order_by('name')

    class Meta:
        model  = User
        fields = ["streaming_platforms"]
