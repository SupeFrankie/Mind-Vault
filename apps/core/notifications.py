from .models import Notification


def create_notification(*, recipient, message, target_url, dedupe_key):
    """Create a user-facing notification once, even when a scheduled run repeats."""
    notification, created = Notification.objects.get_or_create(
        recipient=recipient,
        dedupe_key=dedupe_key,
        defaults={"message": message, "target_url": target_url},
    )
    return notification, created
