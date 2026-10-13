from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.vault.files import file_response
from apps.vault.models import Course, Material
from apps.vault.services import filter_materials

from . import risk, services
from .forms import GroupForm, JoinGroupForm, SharedMaterialEditForm, ShareForm
from .models import Membership, Share, StudyGroup
from .permissions import can_access, require_access, shared_with


def _member_group_or_deny(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if not Membership.objects.filter(group=group, user=request.user).exists():
        audit.record("access.denied", "denied", request=request, target=group, area="sharing", attempted="view_group")
        raise PermissionDenied("You are not a member of this group.")
    return group


# ---------- groups ----------
@login_required
def group_list(request):
    memberships = Membership.objects.filter(user=request.user).select_related("group")
    return render(request, "sharing/group_list.html", {"memberships": memberships})


@login_required
def group_create(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = services.create_group(request, form.cleaned_data["name"])
        messages.success(request, f"Group '{group.name}' created. Share its join code with classmates.")
        return redirect("sharing:group_detail", pk=group.pk)
    return render(request, "vault/form.html", {"form": form, "title": "New study group",
                                                "cancel_url": "/groups/", "submit": "Create group"})


@login_required
def group_join(request):
    form = JoinGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            group, created = services.join_group(request, form.cleaned_data["join_code"])
        except services.ServiceError as exc:
            form.add_error("join_code", str(exc))
        else:
            messages.success(request, f"You joined '{group.name}'." if created else "You are already a member.")
            return redirect("sharing:group_detail", pk=group.pk)
    return render(request, "vault/form.html", {"form": form, "title": "Join a study group",
                                                "cancel_url": "/groups/", "submit": "Join"})


@login_required
def group_detail(request, pk):
    group = _member_group_or_deny(request, pk)
    is_owner = group.owner_id == request.user.pk
    ctx = {
        "group": group,
        "is_owner": is_owner,
        "join_code": group.join_code if is_owner else None,  # only the owner sees the code
        "memberships": group.memberships.select_related("user"),
        "shares": group.shares.select_related("material__owner", "material__unit__course"),
    }
    return render(request, "sharing/group_detail.html", ctx)


@login_required
@require_POST
def group_leave(request, pk):
    group = _member_group_or_deny(request, pk)
    try:
        services.leave_group(request, group)
        messages.success(request, "You left the group.")
        return redirect("sharing:group_list")
    except services.ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("sharing:group_detail", pk=pk)


@login_required
def group_delete(request, pk):
    group = _member_group_or_deny(request, pk)
    if request.method == "POST":
        try:
            services.delete_group(request, group)
        except services.ServiceError as exc:
            messages.error(request, str(exc))
            return redirect("sharing:group_detail", pk=pk)
        messages.success(request, "Group deleted.")
        return redirect("sharing:group_list")
    return render(request, "vault/confirm_delete.html", {
        "object": group, "label": "study group", "cancel_url": f"/groups/{pk}/",
        "detail": "All memberships and shares to this group will be removed."})


@login_required
@require_POST
def member_remove(request, pk, user_id):
    group = _member_group_or_deny(request, pk)
    try:
        services.remove_member(request, group, user_id)
        messages.success(request, "Member removed.")
    except services.ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("sharing:group_detail", pk=pk)


# ---------- sharing a material (owner only) ----------
@login_required
def share_manage(request, material_id):
    material = get_object_or_404(Material.objects.select_related("unit__course"), pk=material_id)
    require_access(request, material, "share")
    form = ShareForm(request.POST or None, user=request.user)
    assessment = None
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            share, created = services.save_share(
                request, material, cd["group"], cd["permission"], cd["allow_download"], cd["confirm_risk"]
            )
        except services.ShareRejected as exc:
            form.add_error(None, str(exc))
            assessment = exc.assessment
        else:
            messages.success(request, "Share created." if created else "Share updated.")
            return redirect("sharing:share_manage", material_id=material.pk)
    ctx = {
        "material": material,
        "form": form,
        "assessment": assessment,
        "shares": material.shares.select_related("group"),
        "risk_rules": {"audience": risk.AUDIENCE_POINTS, "permission": risk.PERMISSION_POINTS,
                       "download": 12, "medium_from": risk.MEDIUM_FROM, "high_from": risk.HIGH_FROM},
    }
    return render(request, "sharing/share_manage.html", ctx)


@login_required
@require_POST
def share_revoke(request, pk):
    share = get_object_or_404(Share.objects.select_related("material"), pk=pk)
    try:
        services.revoke_share(request, share)
        messages.success(request, "Share revoked.")
    except services.ServiceError:
        raise PermissionDenied("Only the owner can revoke this share.")
    return redirect("sharing:share_manage", material_id=share.material_id)


# ---------- shared with me ----------
@login_required
def shared_list(request):
    # Scope to authorised rows first, then narrow with the shared search contract.
    qs = shared_with(request.user).select_related("owner", "unit__course").prefetch_related("tags")
    qs = filter_materials(qs, request.GET)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "sharing/shared_list.html", {
        "page": page, "kinds": Material.Kind.choices, "params": request.GET})


@login_required
def shared_detail(request, material_id):
    material = get_object_or_404(Material.objects.select_related("owner", "unit__course__semester"), pk=material_id)
    require_access(request, material, "view")
    ctx = {
        "material": material,
        "is_owner": material.owner_id == request.user.pk,
        "can_download": material.has_file and can_access(request.user, material, "download"),
        "can_edit": can_access(request.user, material, "edit"),
    }
    return render(request, "sharing/shared_detail.html", ctx)


@login_required
def shared_download(request, material_id):
    material = get_object_or_404(Material, pk=material_id)
    require_access(request, material, "download")
    audit.record("material.download", "allowed", request=request, target=material)
    return file_response(material)


@login_required
def shared_edit(request, material_id):
    material = get_object_or_404(Material, pk=material_id)
    require_access(request, material, "edit")
    form = SharedMaterialEditForm(request.POST or None, instance=material)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record("material.edit_shared", "allowed", request=request, target=material)
        messages.success(request, "Changes saved.")
        return redirect("sharing:shared_detail", material_id=material.pk)
    return render(request, "vault/form.html", {"form": form, "title": f"Edit '{material.title}'",
                                                "cancel_url": f"/shared/{material.pk}/", "submit": "Save"})
