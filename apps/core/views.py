from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from apps.sharing.models import Membership, Share
from apps.vault.models import Material, Semester

from .models import AuditEvent


@login_required
def dashboard(request):
    """Every number here is a real count of the user's own data."""
    user = request.user
    ctx = {
        "semester_count": Semester.objects.filter(owner=user).count(),
        "material_count": Material.objects.filter(owner=user).count(),
        "group_count": Membership.objects.filter(user=user).count(),
        "shared_by_me_count": Share.objects.filter(material__owner=user).count(),
        "shared_with_me_count": Material.objects.filter(shares__group__memberships__user=user)
        .exclude(owner=user)
        .distinct()
        .count(),
        "recent_materials": Material.objects.filter(owner=user).select_related("unit__course")[:5],
        "recent_events": AuditEvent.objects.filter(actor=user)[:5],
    }
    return render(request, "core/dashboard.html", ctx)


@login_required
def activity(request):
    """Users see only their own security events (read-only)."""
    events = AuditEvent.objects.filter(actor=request.user)
    page = Paginator(events, 25).get_page(request.GET.get("page"))
    return render(request, "core/activity.html", {"page": page})


def permission_denied(request, exception=None):
    return render(request, "403.html", {"reason": str(exception) if exception else ""}, status=403)
