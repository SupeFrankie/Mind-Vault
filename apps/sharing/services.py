"""Business rules for groups and shares. Views stay thin; every rule is re-checked here
so that no caller (form, view, future API) can bypass it.

Transactions: only the MUTATION is wrapped in transaction.atomic(). Denial/block audit
events are written BEFORE raising, outside any transaction, otherwise the rollback
triggered by the exception would erase the very record we are trying to keep."""
from dataclasses import replace

from django.db import IntegrityError, connection, transaction

from apps.core import audit
from apps.vault.models import Material

from . import policy, risk
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
            # Group row is the serialization mutex for every membership mutation and
            # share creation. It exists even when the membership row does not.
            locked_group = _lock_group(group.pk)
            if locked_group is None:
                raise ServiceError("That join code is not valid.")
            _, created = Membership.objects.get_or_create(group=locked_group, user=request.user)
    except IntegrityError:  # two simultaneous joins; the unique constraint won
        created = False
    if created:
        audit.record("group.join", "allowed", request=request, target=group)
    return group, created


def leave_group(request, group: StudyGroup):
    with transaction.atomic():
        locked_group = _lock_group(group.pk)
        if locked_group is None:
            raise ServiceError("You are not a member of this group.")
        if locked_group.owner_id == request.user.pk:
            raise ServiceError("The owner cannot leave. Delete the group instead.")
        _drop_member(request, locked_group, request.user, action="group.leave")


def remove_member(request, group: StudyGroup, user_id: int):
    if group.owner_id != request.user.pk:
        audit.record("access.denied", "denied", request=request, target=group, attempted="remove_member")
        raise ServiceError("Only the group owner can remove members.")
    if user_id == group.owner_id:
        raise ServiceError("The owner cannot be removed.")
    denied = False
    with transaction.atomic():
        locked_group = _lock_group(group.pk)
        if locked_group is None:
            raise ServiceError("That user is not a member.")
        if locked_group.owner_id != request.user.pk:
            denied = True
        elif user_id == locked_group.owner_id:
            raise ServiceError("The owner cannot be removed.")
        else:
            membership = Membership.objects.select_for_update().filter(
                group=locked_group, user_id=user_id
            ).first()
            if membership is None:
                raise ServiceError("That user is not a member.")
            _drop_member(request, locked_group, membership.user, action="group.remove_member")
    if denied:
        audit.record("access.denied", "denied", request=request, target=group,
                     attempted="remove_member")
        raise ServiceError("Only the group owner can remove members.")


def _lock_group(group_id):
    """Lock the group row used to serialize membership changes with share writes."""
    return StudyGroup.objects.select_for_update().filter(pk=group_id).first()


def _drop_member(request, group, user, action):
    # Callers hold the group row lock. Lock membership before shares to follow the
    # global order: material -> group -> membership -> share. The group lock also
    # protects the absent-row case and serializes share creation with this cleanup.
    membership = Membership.objects.select_for_update().filter(group=group, user=user).first()
    if membership is None:
        raise ServiceError("You are not a member of this group.")
    # Invariant: a share's creator must belong to the group, so their shares go with them.
    revoked = Share.objects.filter(group=group, material__owner=user).delete()[0]
    membership.delete()
    audit.record(action, "allowed", request=request, target=group, member_id=user.pk, shares_revoked=revoked)


def delete_group(request, group: StudyGroup):
    if group.owner_id != request.user.pk:
        audit.record("access.denied", "denied", request=request, target=group, attempted="delete_group")
        raise ServiceError("Only the group owner can delete the group.")
    denied = False
    with transaction.atomic():
        locked_group = _lock_group(group.pk)
        if locked_group is None:
            return
        if locked_group.owner_id != request.user.pk:
            denied = True
        else:
            audit.record("group.delete", "allowed", request=request, target=locked_group, name=locked_group.name)
            locked_group.delete()  # cascades memberships and shares; audit history stays
    if denied:
        audit.record("access.denied", "denied", request=request, target=group, attempted="delete_group")
        raise ServiceError("Only the group owner can delete the group.")


def save_share(request, material, group, permission: str, allow_download: bool, confirm_risk: bool) -> tuple[Share, bool]:
    """Create or update the share of `material` with `group`. Returns (share, created).

    The server recomputes risk here; nothing the browser says about risk is trusted.
    """
    if permission not in Share.Permission.values:
        raise ShareRejected("Invalid permission.")
    if not isinstance(allow_download, bool):
        raise ShareRejected("allow_download must be a boolean.")
    if not isinstance(confirm_risk, bool):
        raise ShareRejected("Confirmation must be a boolean.")

    user = request.user
    rejection = None
    rejection_audit = None
    with transaction.atomic():
        # Global lock order: material -> group -> membership -> share. The group row
        # serializes this write with join/removal even if the membership row is absent
        # or being deleted. Sensitivity edits take material -> share; group cleanup
        # never waits for material, so these paths do not form a lock cycle.
        stored_material = Material.objects.select_for_update().filter(pk=material.pk).first()
        if stored_material is None or not can_access(user, stored_material, "share"):
            rejection = ("Only the owner can share this material.", None)
            rejection_audit = ("access.denied", material, {"attempted": "share"})
        else:
            locked_group = _lock_group(group.pk)
            if locked_group is None:
                rejection = ("That group no longer exists.", None)
                rejection_audit = ("access.denied", group, {"attempted": "share_to_group"})
            elif Membership.objects.select_for_update().filter(
                group=locked_group, user=user
            ).first() is None:
                rejection = ("You can only share with groups you belong to.", None)
                rejection_audit = ("access.denied", group, {"attempted": "share_to_group"})
            else:
                existing = Share.objects.select_for_update().filter(
                    material=stored_material, group=locked_group
                ).first()
                # Keep identical legacy shares byte-for-byte unchanged until this material's
                # classification transition has reconciled prohibited shares.
                if existing and existing.permission == permission and existing.allow_download == allow_download:
                    return existing, False

                result = risk.assess_share("group", permission, allow_download)
                decision = policy.evaluate_share_policy(stored_material.sensitivity, permission, allow_download)
                if not decision.allowed:
                    if not policy.has_allowed_group_configuration(stored_material.sensitivity):
                        result = replace(result, message=decision.message, recommended=None)
                    rejection = (decision.message, result)
                    rejection_audit = ("share.blocked", stored_material, {
                        "group_id": locked_group.pk,
                        "permission": permission,
                        "allow_download": allow_download,
                        "sensitivity": stored_material.sensitivity,
                        "risk_score": result.score,
                        "risk_level": result.level,
                    })
                elif decision.requires_confirmation and not confirm_risk:
                    rejection = (decision.message + " Please confirm to continue.", result)
                else:
                    detail = dict(group_id=locked_group.pk, permission=permission, allow_download=allow_download,
                                  risk_score=result.score, risk_level=result.level)
                    if existing is None:
                        share = Share.objects.create(
                            material=stored_material, group=locked_group, permission=permission,
                            allow_download=allow_download, created_by=user,
                            risk_score=result.score, risk_level=result.level,
                        )
                        audit.record("share.create", "allowed", request=request, target=stored_material, **detail)
                        return share, True

                    before = dict(permission=existing.permission, allow_download=existing.allow_download)
                    existing.permission, existing.allow_download = permission, allow_download
                    existing.risk_score, existing.risk_level = result.score, result.level
                    existing.save()
                    audit.record("share.update", "allowed", request=request, target=stored_material,
                                 before=before, **detail)
                    return existing, False

    if rejection_audit:
        action, target, detail = rejection_audit
        outcome = "blocked" if action == "share.blocked" else "denied"
        audit.record(action, outcome, request=request, target=target, **detail)
    if rejection:
        raise ShareRejected(*rejection)
    raise ShareRejected("Unable to save sharing settings.")


def _safer_settings_recommendation(material, permission, allow_download):
    """Return only the supported Editor -> Viewer/no-download recommendation."""
    try:
        current_policy = policy.evaluate_share_policy(material.sensitivity, permission, allow_download)
        assessment = risk.assess_share("group", permission, allow_download)
    except (TypeError, ValueError):
        return None
    if not current_policy.allowed or permission != Share.Permission.EDIT:
        return None

    expected = {
        "audience": "group",
        "permission": Share.Permission.VIEW,
        "allow_download": False,
    }
    if assessment.recommended != expected:
        return None
    try:
        recommended_policy = policy.evaluate_share_policy(
            material.sensitivity, expected["permission"], expected["allow_download"]
        )
    except (TypeError, ValueError):
        return None
    # This action has no confirmation input. If policy ever requires confirmation
    # for this target, keep it on the existing explicitly confirmed share workflow.
    if not recommended_policy.allowed or recommended_policy.requires_confirmation:
        return None
    return expected


def safer_settings_available(material, share):
    """Whether the current persisted grant has the one supported safe alternative."""
    return _safer_settings_recommendation(material, share.permission, share.allow_download) is not None


def apply_safer_settings(request, share_id):
    """Apply only the server-derived Editor -> Viewer/no-download change."""
    snapshot = Share.objects.filter(pk=share_id).values("material_id", "group_id").first()
    if snapshot is None:
        raise ShareRejected("This share is no longer available.")

    with transaction.atomic():
        stored_material = Material.objects.select_for_update().filter(pk=snapshot["material_id"]).first()
        if stored_material is None or not can_access(request.user, stored_material, "share"):
            raise ShareRejected("Only the material owner can change sharing settings.")

        locked_group = _lock_group(snapshot["group_id"])
        if locked_group is None:
            raise ShareRejected("The shared group no longer exists.")
        if Membership.objects.select_for_update().filter(
            group=locked_group, user=request.user
        ).first() is None:
            raise ShareRejected("You must remain a member of the group to change its share.")

        share = Share.objects.select_for_update().filter(
            pk=share_id, material=stored_material, group=locked_group
        ).first()
        if share is None:
            raise ShareRejected("This share is no longer available.")

        recommendation = _safer_settings_recommendation(
            stored_material, share.permission, share.allow_download
        )
        if recommendation is None:
            raise ShareRejected("This share has no policy-approved safer settings to apply.")

        before = {"permission": share.permission, "allow_download": share.allow_download}
        assessment = risk.assess_share(
            recommendation["audience"], recommendation["permission"], recommendation["allow_download"]
        )
        share.permission = recommendation["permission"]
        share.allow_download = recommendation["allow_download"]
        share.risk_score = assessment.score
        share.risk_level = assessment.level
        share.save(update_fields=["permission", "allow_download", "risk_score", "risk_level", "updated_at"])
        audit.record(
            "share.update", "allowed", request=request, target=stored_material,
            share_id=share.pk, group_id=locked_group.pk,
            before=before,
            after={"permission": share.permission, "allow_download": share.allow_download},
            risk_score=assessment.score, risk_level=assessment.level,
            reason="safer_settings_recommendation",
        )
        return share


def reconcile_sensitivity_change(request, material, old_sensitivity):
    """Revoke only prohibited shares for a newly saved sensitivity.

    Called by MaterialForm.save() inside its transaction after locking the material
    row. Share rows are locked in primary-key order after the material lock.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Sensitivity transitions must run inside the material-save transaction.")
    stored_material = Material.objects.select_for_update().get(pk=material.pk)
    if request.user.pk != stored_material.owner_id:
        raise ShareRejected("Only the material owner can change sensitivity.")

    new_sensitivity = stored_material.sensitivity
    transitioned = []
    shares = Share.objects.select_for_update().filter(material=stored_material).order_by("pk")
    for share in shares:
        decision = policy.evaluate_share_policy(new_sensitivity, share.permission, share.allow_download)
        if decision.allowed:
            continue
        before = {
            "permission": share.permission,
            "allow_download": share.allow_download,
            "risk_score": share.risk_score,
            "risk_level": share.risk_level,
        }
        transition = {"share_id": share.pk, "group_id": share.group_id, "before": before}
        audit.record(
            "share.revoke", "allowed", request=request, target=stored_material,
            group_id=share.group_id, reason="sensitivity_change",
            old_sensitivity=old_sensitivity, new_sensitivity=new_sensitivity, before=before,
        )
        share.delete()
        transitioned.append(transition)

    audit.record(
        "material.sensitivity_change", "allowed", request=request, target=stored_material,
        old_sensitivity=old_sensitivity, new_sensitivity=new_sensitivity,
        revoked_shares=transitioned,
    )
    return transitioned


def revoke_share(request, share: Share):
    denied = False
    with transaction.atomic():
        material = Material.objects.select_for_update().filter(pk=share.material_id).first()
        if material is None or not can_access(request.user, material, "share"):
            denied = True
        else:
            locked_share = Share.objects.select_for_update().filter(pk=share.pk, material=material).first()
            if locked_share is not None:
                audit.record("share.revoke", "allowed", request=request, target=material,
                             group_id=locked_share.group_id,
                             before={"permission": locked_share.permission,
                                     "allow_download": locked_share.allow_download})
                locked_share.delete()
    if denied:
        audit.record("access.denied", "denied", request=request, target=share.material, attempted="revoke")
        raise ServiceError("Only the owner can revoke this share.")
