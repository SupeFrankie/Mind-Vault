from datetime import datetime, timedelta, timezone as datetime_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.notifications import create_notification
from apps.planner.models import Entry


class Command(BaseCommand):
    help = "Create idempotent in-app reminders for upcoming timed planner entries."

    def add_arguments(self, parser):
        parser.add_argument("--window-minutes", type=int, default=5)

    def handle(self, *args, **options):
        window_minutes = options["window_minutes"]
        if not 1 <= window_minutes < 60:
            raise CommandError("--window-minutes must be between 1 and 59.")

        now = timezone.now()
        window = timedelta(minutes=window_minutes)
        created_count = 0
        local_tz = timezone.get_current_timezone()
        for lead_hours in (24, 1):
            target = now + timedelta(hours=lead_hours)
            window_start = target - window
            start_day = timezone.localtime(window_start, local_tz).date()
            end_day = timezone.localtime(target, local_tz).date()
            day = start_day
            while day <= end_day:
                entries = Entry.objects.filter(all_day=False).filter(
                    Q(event_date=day) |
                    Q(event_date__isnull=True, weekday=day.weekday())
                ).select_related("owner")
                for entry in entries.iterator():
                    occurrence = timezone.make_aware(
                        datetime.combine(day, entry.start_time), local_tz
                    )
                    occurrence_utc = occurrence.astimezone(datetime_timezone.utc)
                    if not (window_start < occurrence_utc <= target):
                        continue
                    lead_label = "1 hour" if lead_hours == 1 else "24 hours"
                    if entry.entry_type == Entry.Type.ASSIGNMENT:
                        message = f"Assignment deadline: {entry.title} in {lead_label}."
                    else:
                        message = f"{entry.title} in {lead_label}."
                    dedupe_key = (
                        f"planner-entry:{entry.pk}:{day.isoformat()}:"
                        f"{entry.start_time.isoformat()}:{lead_hours}h"
                    )
                    _, created = create_notification(
                        recipient=entry.owner,
                        message=message,
                        target_url=reverse("planner:entry_detail", args=[entry.pk]),
                        dedupe_key=dedupe_key,
                    )
                    created_count += int(created)
                day += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS(f"Created {created_count} reminder(s)."))
