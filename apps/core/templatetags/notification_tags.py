from django import template

from apps.core.models import Notification

register = template.Library()


@register.simple_tag(takes_context=True)
def unread_notification_count(context):
    user = context.get("user")
    if user is None or not user.is_authenticated:
        return 0
    count = Notification.objects.filter(recipient=user, read_at__isnull=True).count()
    return "99+" if count > 99 else count
