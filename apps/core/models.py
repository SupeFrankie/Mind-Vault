from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    pass


class AuditQuerySet(models.QuerySet):
    """Blocks bulk update/delete through the ORM so history cannot be rewritten by app code."""

    def update(self, **kwargs):
        raise AppendOnlyError("Audit events are append-only.")

    def delete(self):
        raise AppendOnlyError("Audit events are append-only.")


class AuditEvent(models.Model):
    """Security-relevant event log.

    Lives in `core` (the base layer) so that `vault` can record access denials
    without importing `sharing`. Retention rules:
    - actor is SET_NULL on user deletion, and actor_email keeps who it was;
    - target is stored as type+id (no FK) so deleting a material/group keeps its history.
    NOTE: this is append-only through the application. For stronger guarantees,
    run the app's MySQL user with INSERT/SELECT only on this table (see docs/DEPLOYMENT.md).
    """

    class Outcome(models.TextChoices):
        ALLOWED = "allowed", "Allowed"
        DENIED = "denied", "Denied"
        BLOCKED = "blocked", "Blocked by Security Engine"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=32, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    # Non-sensitive context only (permission levels, risk score...). Never secrets or file contents.
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["action", "outcome"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("Audit events cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit events cannot be deleted.")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} [{self.outcome}]"
