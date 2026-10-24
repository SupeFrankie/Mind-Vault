from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

from apps.core.notifications import create_notification

from .models import Membership, StudyGroup


@receiver(post_save, sender=Membership, dispatch_uid="sharing.notify_members_on_group_join")
def notify_members_on_group_join(sender, instance, created, **kwargs):
    if not created:
        return

    group_id = instance.group_id
    joining_user_id = instance.user_id

    def notify_current_members():
        group = StudyGroup.objects.filter(pk=group_id).first()
        if group is None:
            return
        joined_membership = Membership.objects.filter(
            group=group, user_id=joining_user_id
        ).select_related("user").first()
        if joined_membership is None:
            return
        joiner = joined_membership.user
        display_name = joiner.get_full_name().strip() or joiner.get_short_name()
        target_url = reverse("sharing:group_detail", args=[group.pk])
        memberships = Membership.objects.filter(group=group).exclude(
            user_id=joining_user_id
        ).filter(user__is_active=True).select_related("user")
        for membership in memberships:
            create_notification(
                recipient=membership.user,
                message=f"{display_name} joined {group.name}.",
                target_url=target_url,
                dedupe_key=f"group-join:{group.pk}:{joining_user_id}",
            )

    transaction.on_commit(notify_current_members, robust=True)
