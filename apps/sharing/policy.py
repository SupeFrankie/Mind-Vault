"""Deterministic sharing policy, separate from the explanatory risk heuristic."""
from dataclasses import dataclass

from apps.vault.models import Material
from .models import Share


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    message: str


def evaluate_share_policy(sensitivity: str | None, permission: str, allow_download: bool) -> PolicyDecision:
    """Return the policy decision for one group-share configuration.

    Unknown/unclassified values fail closed. This function validates the raw values
    rather than coercing client input to booleans or accepting unknown permissions.
    """
    if permission not in Share.Permission.values:
        raise ValueError("Invalid permission.")
    if not isinstance(allow_download, bool):
        raise ValueError("allow_download must be a boolean")

    no_download_view = permission == Share.Permission.VIEW and not allow_download
    edit = permission == Share.Permission.EDIT

    if sensitivity == Material.Sensitivity.PUBLIC:
        return PolicyDecision(True, edit, "Edit access requires explicit confirmation." if edit else "Allowed by policy.")
    if sensitivity == Material.Sensitivity.INTERNAL:
        confirmation = allow_download or edit
        return PolicyDecision(True, confirmation,
                              "Downloads or edit access require explicit confirmation."
                              if confirmation else "Allowed by policy.")
    if sensitivity == Material.Sensitivity.CONFIDENTIAL:
        if no_download_view:
            return PolicyDecision(True, True, "Confidential material requires explicit confirmation to share.")
        return PolicyDecision(False, False, "Confidential material may only be shared view-only without downloads.")
    if sensitivity == Material.Sensitivity.HIGHLY_CONFIDENTIAL:
        return PolicyDecision(False, False, "Highly confidential material cannot be shared with groups.")
    return PolicyDecision(False, False, "Set a sensitivity level before sharing this material.")


def has_allowed_group_configuration(sensitivity: str | None) -> bool:
    """Whether any group-share combination is permitted at this sensitivity."""
    return any(
        evaluate_share_policy(sensitivity, permission, allow_download).allowed
        for permission in Share.Permission.values
        for allow_download in (False, True)
    )
