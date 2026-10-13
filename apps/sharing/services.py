"""Business rules for groups and shares. Views stay thin; every rule is re-checked here
so that no caller (form, view, future API) can bypass it.

Transactions: only the MUTATION is wrapped in transaction.atomic(). Denial/block audit
events are written BEFORE raising, outside any transaction, otherwise the rollback
triggered by the exception would erase the very record we are trying to keep."""
from django.db import IntegrityError, transaction

from apps.core import audit

from . import risk
from .models import Membership, Share, StudyGroup, generate_join_code
from .permissions import can_access


class ServiceError(Exception):
    """A user-presentable rule violation."""


class ShareRejected(ServiceError):
    def __init__(self, message, assessment=None):
        super().__init__(message)
        self.assessment = assessment


@transaction.atomic
def create_group(request, name: str) -> StudyGroup:
    user = request.user
    for _ in range(5):  # collisions are astronomically rare; retry rather than crash
        try:
            with transaction.atomic():
                group = StudyGroup.objects.create(name=name.strip(), owner=user, join_code=generate_join_code())
            break
        except IntegrityError:
            continue
    else:
        raise ServiceError("Could not generate a join code. Try again.")
    Membership.objects.create(group=group, user=user, role=Membership.Role.OWNER)
    audit.record("group.create", "allowed", request=request, target=group)
    return group


def join_group(request, code: str):
    """Returns (group, created). Failed attempts are audited (basic brute-force trail)."""
    code = (code or "").strip().upper()
    group = StudyGroup.objects.filter(join_code=code).first() if code else None
    if group is None:
        audit.record("group.join_failed", "denied", request=request)
        raise ServiceError("That join code is not valid.")
    try:
        with transaction.atomic():
            _, created = Membership.objects.get_or_create(group=group, user=request.user)
    except IntegrityError:  # two simultaneous joins; the unique constraint won
        created = False
    if created:
        audit.record("group.join", "allowed", request=request, target=group)
    return group, created


def leave_group(request, group: StudyGroup):
    membership = Membership.objects.filter(group=group, user=request.user).first()
    if membership is None:
        raise ServiceError("You are not a member of this group.")
    if group.owner_id == request.user.pk:
        raise ServiceError("The owner cannot leave. Delete the group instead.")
    with transaction.atomic():
        _drop_member(request, group, request.user, action="group.leave")


def remove_member(request, group: StudyGroup, user_id: int):
    if group.owner_id != request.user.pk:
        audit.record("access.denied", "denied", request=request, target=group, attempted="remove_member")
        raise ServiceError("Only the group owner can remove members.")
    if user_id == group.owner_id:
        raise ServiceError("The owner cannot be removed.")
    membership = Membership.objects.select_related("user").filter(group=group, user_id=user_id).first()
    if membership is None:
        raise ServiceError("That user is not a member.")
    with transaction.atomic():
        _drop_member(request, group, membership.user, action="group.remove_member")


def _drop_member(request, group, user, action):
    # Invariant: a share's creator must belong to the group, so their shares go with them.
    revoked = Share.objects.filter(group=group, material__owner=user).delete()[0]
    Membership.objects.filter(group=group, user=user).delete()
    audit.record(action, "allowed", request=request, target=group, member_id=user.pk, shares_revoked=revoked)


def delete_group(request, group: StudyGroup):
    if group.owner_id != request.user.pk:
        audit.record("access.denied", "denied", request=request, target=group, attempted="delete_group")
        raise ServiceError("Only the group owner can delete the group.")
    with transaction.atomic():
        audit.record("group.delete", "allowed", request=request, target=group, name=group.name)
        group.delete()  # cascades memberships and shares; audit history stays


def save_share(request, material, group, permission: str, allow_download: bool, confirm_risk: bool) -> tuple[Share, bool]:
    """Create or update the share of `material` with `group`. Returns (share, created).

    The server recomputes risk here; nothing the browser says about risk is trusted.
    """
    user = request.user
    if not can_access(user, material, "share"):
        audit.record("access.denied", "denied", request=request, target=material, attempted="share")
        raise ShareRejected("Only the owner can share this material.")
    if not Membership.objects.filter(group=group, user=user).exists():
        audit.record("access.denied", "denied", request=request, target=group, attempted="share_to_group")
        raise ShareRejected("You can only share with groups you belong to.")
    if permission not in Share.Permission.values:
        raise ShareRejected("Invalid permission.")

    try:
        result = risk.assess_share("group", permission, bool(allow_download))
    except ValueError as exc:
        raise ShareRejected(str(exc))

    detail = dict(group_id=group.pk, permission=permission, allow_download=bool(allow_download),
                  risk_score=result.score, risk_level=result.level)
    if not result.allowed:
        audit.record("share.blocked", "blocked", request=request, target=material, **detail)
        raise ShareRejected(result.message, result)
    if result.requires_confirmation and not confirm_risk:
        raise ShareRejected("Please confirm you accept the medium risk, or choose safer settings.", result)

    with transaction.atomic():
        existing = Share.objects.select_for_update().filter(material=material, group=group).first()
        if existing is None:
            share = Share.objects.create(
                material=material, group=group, permission=permission, allow_download=bool(allow_download),
                created_by=user, risk_score=result.score, risk_level=result.level,
            )
            audit.record("share.create", "allowed", request=request, target=material, **detail)
            return share, True

        before = dict(permission=existing.permission, allow_download=existing.allow_download)
        existing.permission, existing.allow_download = permission, bool(allow_download)
        existing.risk_score, existing.risk_level = result.score, result.level
        existing.save()
        audit.record("share.update", "allowed", request=request, target=material, before=before, **detail)
        return existing, False


def revoke_share(request, share: Share):
    if not can_access(request.user, share.material, "share"):
        audit.record("access.denied", "denied", request=request, target=share.material, attempted="revoke")
        raise ServiceError("Only the owner can revoke this share.")
    with transaction.atomic():
        audit.record("share.revoke", "allowed", request=request, target=share.material, group_id=share.group_id)
        share.delete()
