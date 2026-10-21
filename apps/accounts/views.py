from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

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


# Placeholder sign-in options. Nothing here authenticates, redirects off-site or collects credentials.
# Real OAuth/OIDC/SAML integration needs provider credentials and (for school email) institutional approval.
COMING_SOON_PROVIDERS = {
    "google": {
        "name": "Google",
        "message": "Google sign-in is coming soon. For now, please use the existing sign-in method.",
    },
    "apple": {
        "name": "Apple",
        "message": "Apple sign-in is coming soon. For now, please use the existing sign-in method.",
    },
    "school-email": {
        "name": "School email",
        "message": "School-email authentication is coming soon. We plan to explore authorised "
        "integration with university authentication systems.",
    },
}


@require_GET
def coming_soon(request, provider):
    info = COMING_SOON_PROVIDERS.get(provider)
    if info is None:  # allowlist lookup: unknown names are a plain 404, never an error
        raise Http404("Unknown sign-in option.")
    return render(request, "accounts/coming_soon.html", info)
