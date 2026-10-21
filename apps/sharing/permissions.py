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

from . import policy
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
    grants = effective_grants(user, material)
    if action == "view":
        return bool(grants)
    if action == "download":
        return any(share.allow_download for share in grants)
    return any(share.permission == Share.Permission.EDIT for share in grants)  # action == "edit"


def effective_grants(user, material):
    """Return this user's current group grants that remain valid under policy.

    This is shared by authorization and the owner-facing access review so the UI
    cannot advertise a grant the request authorization path would reject.
    Confirmation is a creation/update-time requirement; an already persisted
    allowed configuration remains effective.
    """
    if user is None or not user.is_authenticated:
        return []
    grants = Share.objects.filter(
        material=material, group__memberships__user=user
    ).select_related("group")
    eligible = []
    for share in grants:
        try:
            decision = policy.evaluate_share_policy(
                material.sensitivity, share.permission, share.allow_download
            )
        except (TypeError, ValueError):
            # Malformed or outdated persisted rows must fail closed.
            continue
        if decision.allowed:
            eligible.append(share)
    return eligible


def effective_access_review(material):
    """Aggregate current, policy-valid group grants by person for the owner UI."""
    shares = list(
        Share.objects.filter(material=material)
        .select_related("group")
        .prefetch_related("group__memberships__user")
    )
    grants_by_user = {}
    for share in shares:
        try:
            decision = policy.evaluate_share_policy(
                material.sensitivity, share.permission, share.allow_download
            )
        except (TypeError, ValueError):
            continue
        if not decision.allowed:
            continue
        for membership in share.group.memberships.all():
            user = membership.user
            if user.pk == material.owner_id:
                continue
            grants = grants_by_user.setdefault(user.pk, {"user": user, "shares": []})
            grants["shares"].append(share)

    entries = [{
        "user": material.owner,
        "is_owner": True,
        "access_level": "Owner",
        "download_allowed": True,
        "view_groups": [],
        "edit_groups": [],
        "download_groups": [],
        "multiple_groups": False,
    }]
    for grants in grants_by_user.values():
        user = grants["user"]
        user_shares = grants["shares"]
        def group_names(selected_shares):
            # Group names are not unique; key by primary key so each granting
            # group remains represented even when two groups share a display name.
            names_by_id = {share.group_id: share.group.name for share in selected_shares}
            return sorted(names_by_id.values(), key=str.casefold)

        entries.append({
            "user": user,
            "is_owner": False,
            "access_level": "Editor" if any(
                share.permission == Share.Permission.EDIT for share in user_shares
            ) else "Viewer",
            "download_allowed": any(share.allow_download for share in user_shares),
            "view_groups": group_names(user_shares),
            "edit_groups": group_names(share for share in user_shares
                                       if share.permission == Share.Permission.EDIT),
            "download_groups": group_names(share for share in user_shares if share.allow_download),
            "multiple_groups": len({share.group_id for share in user_shares}) > 1,
        })
    entries[1:] = sorted(entries[1:], key=lambda entry: (
        entry["user"].get_full_name().casefold() or entry["user"].get_username().casefold()
    ))
    return entries


def require_access(request, material, action: str):
    """Raise 403 (and write an audit event) when access is not allowed."""
    if not can_access(request.user, material, action):
        audit.record("access.denied", "denied", request=request, target=material,
                     area="sharing", attempted=action, method=request.method)
        raise PermissionDenied("You do not have permission to do that.")


def shared_with(user):
    """Materials other people shared with groups `user` belongs to. Used for list/search,
    so only policy-eligible rows ever reach the template."""
    eligible_grant = _policy_eligible_share_filter()
    return (
        Material.objects.filter(
            Q(shares__group__memberships__user=user) & eligible_grant
        )
        .exclude(owner=user)
        .distinct()
    )


def _policy_eligible_share_filter():
    """Build a database filter from the deterministic policy's allowed configs."""
    eligible = Q(pk__in=[])
    for sensitivity in Material.Sensitivity.values:
        for permission in Share.Permission.values:
            for allow_download in (False, True):
                decision = policy.evaluate_share_policy(sensitivity, permission, allow_download)
                if decision.allowed:
                    eligible |= Q(
                        sensitivity=sensitivity,
                        shares__permission=permission,
                        shares__allow_download=allow_download,
                    )
    return eligible


def visible_to(user):
    return Material.objects.filter(Q(owner=user) | Q(shares__group__memberships__user=user)).distinct()
