import json
import logging
import jwt
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from clerk_backend_api import Clerk

from .forms import EmailLoginForm, ProfileForm, RegisterForm

logger = logging.getLogger(__name__)


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


def sso_callback(request):
    """Callback route that handles Clerk OAuth / Google redirects."""
    return render(request, "accounts/sso_callback.html")


@csrf_exempt
@require_http_methods(["POST"])
def clerk_login(request):
    """Authenticates the session token from Clerk, finds or creates the Django user,
    and establishes the Django session.
    """
    token = None
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body.decode("utf-8"))
            token = data.get("token")
        except Exception:
            pass
    if not token:
        token = request.POST.get("token")
    if not token:
        token = request.COOKIES.get("__session")
    if not token and "HTTP_AUTHORIZATION" in request.META:
        auth_header = request.META["HTTP_AUTHORIZATION"]
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        return JsonResponse({"status": "error", "message": "Missing session token."}, status=400)

    secret_key = getattr(settings, "CLERK_SECRET_KEY", "")
    if not secret_key:
        return JsonResponse({"status": "error", "message": "Clerk secret key is not configured."}, status=500)

    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        user_id = claims.get("sub")
        if not user_id:
            return JsonResponse({"status": "error", "message": "Invalid token claims."}, status=400)

        clerk = Clerk(bearer_auth=secret_key)
        clerk_user = clerk.users.get(user_id=user_id)

        email = None
        if clerk_user.email_addresses:
            for em in clerk_user.email_addresses:
                email = em.email_address
                break

        if not email:
            return JsonResponse({"status": "error", "message": "No email address found for Clerk user."}, status=400)

        full_name = f"{clerk_user.first_name or ''} {clerk_user.last_name or ''}".strip()
        if not full_name:
            full_name = clerk_user.username or email.split("@")[0]

        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            user = User.objects.create_user(email=email.lower(), full_name=full_name)

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        next_url = request.GET.get("next") or request.POST.get("next")
        if not next_url and request.content_type == "application/json":
            try:
                next_url = data.get("next")
            except Exception:
                pass
        return JsonResponse({"status": "success", "redirect_url": next_url or "/"})
    except Exception as e:
        logger.exception("Clerk login error: %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


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
