"""The ONE place access decisions are made for materials.

Rules:
- The owner can do everything.
- A non-owner gets access only through a Share to a group they currently belong to.
- Group membership alone grants nothing: a private material has no Share row.
- view / download / edit are independent: edit does not imply download.
- replace_file, delete and share are owner-only.
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core import audit
from apps.vault.models import Material

from .models import Share

ACTIONS = {"view", "download", "edit", "replace_file", "delete", "share"}
OWNER_ONLY = {"replace_file", "delete", "share"}


def can_access(user, material, action: str) -> bool:
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")  # typo must not silently mean "deny"/"allow"
    if user is None or not user.is_authenticated:
        return False
    if material.owner_id == user.pk:
        return True
    if action in OWNER_ONLY:
        return False
    grants = Share.objects.filter(material=material, group__memberships__user=user)
    if action == "view":
        return grants.exists()
    if action == "download":
        return grants.filter(allow_download=True).exists()
    return grants.filter(permission=Share.Permission.EDIT).exists()  # action == "edit"


def require_access(request, material, action: str):
    """Raise 403 (and write an audit event) when access is not allowed."""
    if not can_access(request.user, material, action):
        audit.record("access.denied", "denied", request=request, target=material,
                     area="sharing", attempted=action, method=request.method)
        raise PermissionDenied("You do not have permission to do that.")


def shared_with(user):
    """Materials other people shared with groups `user` belongs to. Used for list/search,
    so only authorised rows ever reach the template."""
    return (
        Material.objects.filter(shares__group__memberships__user=user)
        .exclude(owner=user)
        .distinct()
    )


def visible_to(user):
    return Material.objects.filter(Q(owner=user) | Q(shares__group__memberships__user=user)).distinct()
