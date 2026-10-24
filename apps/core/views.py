from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.sharing.models import Membership, Share
from apps.sharing.permissions import shared_with
from apps.vault.models import Material, Semester

from .models import AuditEvent, Notification


@login_required
def dashboard(request):
    """Every number here is a real count of the user's own data."""
    from apps.planner.services import dashboard_entries

    user = request.user
    today, today_entries, next_entry = dashboard_entries(user)
    ctx = {
        "semester_count": Semester.objects.filter(owner=user).count(),
        "material_count": Material.objects.filter(owner=user).count(),
        "group_count": Membership.objects.filter(user=user).count(),
        "shared_by_me_count": Share.objects.filter(material__owner=user).count(),
        "shared_with_me_count": shared_with(user).count(),
        "recent_materials": Material.objects.filter(owner=user).select_related("unit__course")[:5],
        "recent_events": AuditEvent.objects.filter(actor=user)[:5],
        "planner_today": today,
        "planner_today_entries": today_entries[:5],
        "planner_next_entry": next_entry,
    }
    return render(request, "core/dashboard.html", ctx)


@login_required
def activity(request):
    """Users see only their own security events (read-only)."""
    events = AuditEvent.objects.filter(actor=request.user)
    page = Paginator(events, 25).get_page(request.GET.get("page"))
    return render(request, "core/activity.html", {"page": page})


@login_required
def notifications(request):
    items = Notification.objects.filter(recipient=request.user)
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    upcoming = []
    try:
        from apps.planner.services import upcoming_events
        upcoming = upcoming_events(request.user)
    except Exception:
        pass

    return render(request, "core/notifications.html", {
        "page": page,
        "upcoming_events": upcoming,
    })


@login_required
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect("core:notifications")


@login_required
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(read_at=timezone.now())
    messages.success(request, "All notifications marked as read.")
    return redirect("core:notifications")


def permission_denied(request, exception=None):
    return render(request, "403.html", {"reason": str(exception) if exception else ""}, status=403)


LAST_UPDATED = "21 October 2026"


def privacy_policy(request):
    """Public page. Values that describe real behaviour are read from settings, not retyped."""
    return render(request, "core/privacy.html", {
        "last_updated": LAST_UPDATED,
        "max_upload_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
        "session_days": settings.SESSION_COOKIE_AGE // 86400,
    })


def cookie_policy(request):
    return render(request, "core/cookies.html", {
        "last_updated": LAST_UPDATED,
        "session_cookie": settings.SESSION_COOKIE_NAME,
        "csrf_cookie": settings.CSRF_COOKIE_NAME,
        "messages_cookie": CookieStorage.cookie_name,
        "session_days": settings.SESSION_COOKIE_AGE // 86400,
        "csrf_days": settings.CSRF_COOKIE_AGE // 86400,
        "samesite": settings.SESSION_COOKIE_SAMESITE,
        "session_httponly": settings.SESSION_COOKIE_HTTPONLY,
        "csrf_httponly": settings.CSRF_COOKIE_HTTPONLY,
        "secure_cookies": settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE,
    })
