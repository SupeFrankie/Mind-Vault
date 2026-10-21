from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core import audit

from . import services
from .models import Membership, Share, StudyGroup


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    readonly_fields = ("join_code",)

    def delete_model(self, request, obj):
        # Match share writes and membership changes: group is the serialization
        # mutex and must be locked before Django's collector touches dependents.
        with transaction.atomic():
            group = StudyGroup.objects.select_for_update().filter(pk=obj.pk).first()
            if group is not None:
                audit.record(
                    "group.delete", "allowed", request=request, target=group,
                    name=group.name, owner_id=group.owner_id,
                    memberships_removed=group.memberships.count(),
                    shares_removed=group.shares.count(),
                )
                super().delete_model(request, group)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            group_ids = sorted(set(queryset.values_list("pk", flat=True)))
            if group_ids:
                # Acquire every root lock before the collector starts any cascade.
                groups = list(StudyGroup.objects.select_for_update().filter(
                    pk__in=group_ids
                ).order_by("pk"))
                for group in groups:
                    audit.record(
                        "group.delete", "allowed", request=request, target=group,
                        name=group.name, owner_id=group.owner_id,
                        memberships_removed=group.memberships.count(),
                        shares_removed=group.shares.count(),
                    )
                locked_ids = [group.pk for group in groups]
                if locked_ids:
                    super().delete_queryset(request, queryset.filter(pk__in=locked_ids))


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "user", "role", "joined_at")
    readonly_fields = ("joined_at",)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # A membership's identity cannot be moved through admin. Removal and
            # addition are separate, group-locked operations.
            fields.extend(("group", "user"))
        return fields

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            previous = None
            if change:
                previous = Membership.objects.get(pk=obj.pk)
                if (obj.group_id, obj.user_id) != (previous.group_id, previous.user_id):
                    raise ValidationError("Change membership by removing and re-adding it.")
            group = StudyGroup.objects.select_for_update().filter(pk=obj.group_id).first()
            if group is None:
                raise ValidationError("The study group no longer exists.")
            super().save_model(request, obj, form, change)
            if previous is None:
                audit.record(
                    "group.member_add", "allowed", request=request, target=group,
                    membership_id=obj.pk, member_id=obj.user_id, role=obj.role,
                )
            elif obj.role != previous.role:
                audit.record(
                    "group.member_role_change", "allowed", request=request, target=group,
                    membership_id=obj.pk, member_id=obj.user_id,
                    before={"role": previous.role}, after={"role": obj.role},
                )

    def delete_model(self, request, obj):
        with transaction.atomic():
            group = StudyGroup.objects.select_for_update().filter(pk=obj.group_id).first()
            if group is None:
                return
            membership = Membership.objects.select_for_update().filter(
                group=group, user_id=obj.user_id
            ).first()
            if membership is not None:
                services._drop_member(request, group, membership.user, action="group.remove_member")

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            pairs = list(queryset.values_list("group_id", "user_id"))
            groups = {}
            for group_id in sorted({group_id for group_id, _ in pairs}):
                group = StudyGroup.objects.select_for_update().filter(pk=group_id).first()
                if group is not None:
                    groups[group_id] = group
            for group_id, user_id in sorted(pairs):
                group = groups.get(group_id)
                if group is None:
                    continue
                membership = Membership.objects.select_for_update().filter(
                    group=group, user_id=user_id
                ).first()
                if membership is not None:
                    services._drop_member(request, group, membership.user, action="group.remove_member")


@admin.register(Share)
class ShareAdmin(admin.ModelAdmin):
    """Keep admin visibility while routing share changes through the service layer."""
    list_display = ("material", "group", "permission", "allow_download", "created_by")
    readonly_fields = tuple(field.name for field in Share._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
