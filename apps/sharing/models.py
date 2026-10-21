import secrets

from django.conf import settings
from django.db import models

from apps.vault.models import Material

# No 0/O/1/I/L to avoid transcription mistakes. 8 chars from 32 symbols = 40 bits.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(8))


class StudyGroup(models.Model):
    name = models.CharField(max_length=100)
    # PROTECT: deleting a user must not silently orphan or destroy a group others rely on.
    # The owner must delete the group first (this also deletes its shares).
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_groups")
    join_code = models.CharField(max_length=12, unique=True, default=generate_join_code, editable=False)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="study_groups")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="uniq_membership_group_user")]
        indexes = [models.Index(fields=["user", "group"])]

    def __str__(self):
        return f"{self.user} in {self.group} ({self.role})"


class Share(models.Model):
    """Grants a study group access to ONE material with explicit permissions.

    Audience is always a group in the MVP (no public links, no per-user shares), so
    there is no audience column; the Security Engine still scores 'group'.
    Invariants (enforced in services.py): creator owns the material and is a member of
    the group. One row per (material, group): changing settings updates that row.
    """

    class Permission(models.TextChoices):
        VIEW = "view", "View"
        EDIT = "edit", "Edit notes and title"

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="shares")
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="shares")
    permission = models.CharField(max_length=10, choices=Permission.choices, default=Permission.VIEW)
    # Independent of `permission`: edit does NOT imply download.
    allow_download = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="shares_created"
    )
    risk_score = models.PositiveSmallIntegerField(default=0)
    risk_level = models.CharField(max_length=10, default="low")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["material", "group"], name="uniq_share_material_group")]
        indexes = [models.Index(fields=["group", "material"])]

    def __str__(self):
        return f"{self.material} -> {self.group} ({self.permission})"


class Comment(models.Model):
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="material_comments"
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"Comment by {self.author} on {self.material}"
