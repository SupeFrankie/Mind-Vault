"""Single entry point for writing audit events."""
from .models import AuditEvent


def record(action, outcome, *, request=None, actor=None, target=None, **detail):
    """Append an audit event.

    `target` is any model instance (stored as type name + pk). `detail` must stay
    non-sensitive: never pass passwords, session ids or file contents.
    """
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    return AuditEvent.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") if actor else "",
        action=action,
        outcome=outcome,
        target_type=target.__class__.__name__.lower() if target is not None else "",
        target_id=getattr(target, "pk", None),
        detail=detail,
        # REMOTE_ADDR only: X-Forwarded-For is client-controlled unless a trusted proxy sets it.
        ip_address=request.META.get("REMOTE_ADDR") if request is not None else None,
    )
