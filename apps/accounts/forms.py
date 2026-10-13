from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm

User = get_user_model()


class EmailLoginForm(AuthenticationForm):
    """Field name stays `username` (Django contract) but it holds the email."""

    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, strip=False)

    class Meta:
        model = User
        fields = ["email", "full_name", "university"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            # Runs Django's validators (length, common, numeric, similarity).
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "university"]  # email is read-only: it is the login id.
