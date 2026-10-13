from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import EmailLoginForm, ProfileForm, RegisterForm


class MindVaultLoginView(LoginView):
    authentication_form = EmailLoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class MindVaultLogoutView(LogoutView):
    """POST-only (Django 5): logout via GET link would allow CSRF-style forced logout."""


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome to MindVault. Your account is ready.")
        return redirect("core:dashboard")
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})
