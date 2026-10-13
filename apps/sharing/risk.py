"""Security Engine: an application-defined risk heuristic for sharing decisions.

This is NOT a scientifically validated score. It is a transparent additive model
(points per setting) taken from the MindVault pitch deck so that the live meter in
the UI and the server agree. Pure Python: no HTTP, no database, trivially testable.

Points (frontend may mirror these for display; the SERVER is the only authority):
    audience   only_me=0   group=15   public=40
    permission view=5      edit=30                (deck's "comment" level is not implemented)
    download   False=0     True=12
Levels:  score <= 34 low | 35..59 medium | >= 60 high
Policy:  public audience -> blocked (no public links in the MVP)
         high            -> blocked
         medium          -> allowed only with explicit confirmation from the user
         low             -> allowed
"""
from dataclasses import dataclass

AUDIENCE_POINTS = {"only_me": 0, "group": 15, "public": 40}
PERMISSION_POINTS = {"view": 5, "edit": 30}
DOWNLOAD_POINTS = {False: 0, True: 12}
MEDIUM_FROM = 35
HIGH_FROM = 60

LOW, MEDIUM, HIGH = "low", "medium", "high"


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    level: str
    allowed: bool
    requires_confirmation: bool
    message: str
    # Lowest-privilege alternative; None when the requested settings are already that.
    recommended: dict | None


def assess_share(audience: str, permission: str, allow_download: bool) -> RiskAssessment:
    """Raises ValueError on any value outside the allowlists (never guesses)."""
    if audience not in AUDIENCE_POINTS:
        raise ValueError(f"Invalid audience: {audience!r}")
    if permission not in PERMISSION_POINTS:
        raise ValueError(f"Invalid permission: {permission!r}")
    if not isinstance(allow_download, bool):
        raise ValueError("allow_download must be a boolean")

    score = AUDIENCE_POINTS[audience] + PERMISSION_POINTS[permission] + DOWNLOAD_POINTS[allow_download]
    level = HIGH if score >= HIGH_FROM else MEDIUM if score >= MEDIUM_FROM else LOW

    safest = {
        "audience": "group" if audience == "public" else audience,
        "permission": "view",
        "allow_download": False,
    }
    current = {"audience": audience, "permission": permission, "allow_download": allow_download}
    recommended = None if safest == current else safest

    if audience == "public":
        return RiskAssessment(score, level, False, False,
                              "Public sharing is not allowed. Share with a study group instead.", recommended)
    if level == HIGH:
        return RiskAssessment(score, level, False, False,
                              "High risk: this combination exposes the material too widely. "
                              "Use view-only with downloads disabled.", recommended)
    if level == MEDIUM:
        return RiskAssessment(score, level, True, True,
                              "Medium risk: members could change or copy this material. "
                              "Consider view-only or disabling downloads, or confirm to continue.", recommended)
    return RiskAssessment(score, level, True, False, "Low risk: least-privilege settings applied.", recommended)
