import heapq
import math
from datetime import datetime, time, timedelta, timezone as datetime_timezone

from django.db.models import Q
from django.utils import timezone

from apps.core.models import Notification

from .models import Entry


def entry_occurs_on(entry, day):
    return entry.event_date == day or (entry.weekday is not None and entry.weekday == day.weekday())


def occurrence_start(entry, day):
    """Return the local start for a timed event; all-day entries have no start time."""
    if entry.all_day:
        return None
    return timezone.make_aware(datetime.combine(day, entry.start_time), timezone.get_current_timezone())


def overlaps_for(owner, *, event_date, weekday, all_day, start_time, end_time, exclude_pk=None):
    """Return advisory conflicts for a date or every future occurrence of a weekday."""
    qs = Entry.objects.filter(owner=owner)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if event_date:
        qs = qs.filter(models_q_for_day(event_date))
    else:
        # A recurring entry can conflict with a one-off event on any future date
        # having the recurring weekday. ISO weekday numbering is Monday=1..Sunday=7
        # on both supported databases (SQLite and MySQL).
        today = timezone.localdate()
        now_time = timezone.localtime().time().replace(tzinfo=None)
        future_dates = Q(event_date__gt=today)
        if start_time and start_time > now_time:
            future_dates |= Q(event_date=today)
        qs = qs.filter(
            Q(event_date__isnull=True, weekday=weekday)
            | (future_dates & Q(event_date__iso_week_day=weekday + 1))
        )
    if not all_day:
        qs = qs.filter(
            Q(all_day=True)
            | Q(start_time__lt=end_time, end_time__gt=start_time)
        )
    return list(qs.select_related("course").order_by("event_date", "weekday", "start_time", "pk"))


def models_q_for_day(day):
    return Q(event_date=day) | Q(event_date__isnull=True, weekday=day.weekday())


def dashboard_entries(owner):
    """Return the first five of today's agenda and the next upcoming event.

    Today's all-day entries belong in the agenda but do not become midnight
    events. Future all-day entries are date-level next-up candidates and carry
    ``starts=None``. A timed event that has started, including one in progress,
    is not upcoming.
    """
    today = timezone.localdate()
    current = timezone.localtime()
    now_time = current.time().replace(tzinfo=None)

    today_rows = list(
        Entry.objects.filter(owner=owner).filter(
            Q(event_date=today) | Q(event_date__isnull=True, weekday=today.weekday())
        ).select_related("course").order_by("-all_day", "start_time", "title", "pk")[:5]
    )

    # The first future dated event is found directly from the database, no matter
    # how far ahead it is. Skip today's all-day entries and already-started times.
    future_dates = Q(event_date__gt=today) | Q(event_date=today, all_day=False, start_time__gt=now_time)
    dated = (
        Entry.objects.filter(owner=owner, event_date__isnull=False)
        .filter(future_dates)
        .select_related("course")
        .order_by("event_date", "-all_day", "start_time", "pk")
        .first()
    )
    candidates = []
    if dated is not None:
        starts = occurrence_start(dated, dated.event_date)
        candidates.append((dated.event_date, 0 if dated.all_day else 1,
                           dated.start_time or time.min, starts, dated))

    # At most seven indexed lookups find the next timed event for each recurring
    # weekday. This avoids expanding an unbounded series or scanning dates one by one.
    for weekday in range(7):
        offset = (weekday - today.weekday()) % 7
        if weekday == today.weekday():
            recurring = Entry.objects.filter(
                owner=owner, event_date__isnull=True, weekday=weekday,
                all_day=False, start_time__gt=now_time,
            ).select_related("course").order_by("start_time", "pk").first()
            if recurring is None:
                recurring = Entry.objects.filter(
                    owner=owner, event_date__isnull=True, weekday=weekday,
                    all_day=False,
                ).select_related("course").order_by("start_time", "pk").first()
                if recurring is not None:
                    offset = 7
        else:
            recurring = Entry.objects.filter(
                owner=owner, event_date__isnull=True, weekday=weekday,
                all_day=False,
            ).select_related("course").order_by("start_time", "pk").first()
        if recurring is not None:
            day = today + timedelta(days=offset)
            starts = occurrence_start(recurring, day)
            candidates.append((day, 1, recurring.start_time, starts, recurring))

    upcoming = None
    if candidates:
        day, _, _, starts, row = min(candidates, key=lambda item: (item[0], item[1], item[2], item[4].pk))
        upcoming = {"entry": row, "date": day, "starts": starts}
    return today, today_rows, upcoming


def upcoming_events(owner, *, now=None, limit=20):
    """Return the next timed occurrences, excluding occurrences with saved reminders.

    Dated entries are read in database order and streamed; recurring entries
    contribute their next occurrence. The result is deliberately bounded for
    the inbox, while dated entries are not restricted to a short look-ahead.
    """
    now = now or timezone.now()
    local_tz = timezone.get_current_timezone()
    local_now = timezone.localtime(now, local_tz)
    today = local_now.date()
    now_utc = local_now.astimezone(datetime_timezone.utc)
    now_time = local_now.time().replace(tzinfo=None)

    heap = []

    def add_candidate(entry, day, saved_keys):
        starts = timezone.make_aware(datetime.combine(day, entry.start_time), local_tz)
        starts_utc = starts.astimezone(datetime_timezone.utc)
        if starts_utc <= now_utc:
            return
        reminder_keys = (
            f"planner-entry:{entry.pk}:{day.isoformat()}:{entry.start_time.isoformat()}:24h",
            f"planner-entry:{entry.pk}:{day.isoformat()}:{entry.start_time.isoformat()}:1h",
        )
        if any(key in saved_keys for key in reminder_keys):
            return
        item = {
            "entry": entry,
            "date": day,
            "starts": starts,
            "time_until": time_until_label(starts_utc - now_utc),
        }
        # Keep only the closest `limit` items without loading an unbounded
        # number of future dated records into memory.
        rank = (starts_utc.timestamp(), entry.pk)
        heap_item = (-rank[0], -rank[1], item)
        if len(heap) < limit:
            heapq.heappush(heap, heap_item)
        elif rank < (-heap[0][0], -heap[0][1]):
            heapq.heapreplace(heap, heap_item)

    def process_batch(batch):
        if not batch:
            return
        keys = []
        for entry, day in batch:
            keys.extend((
                f"planner-entry:{entry.pk}:{day.isoformat()}:{entry.start_time.isoformat()}:24h",
                f"planner-entry:{entry.pk}:{day.isoformat()}:{entry.start_time.isoformat()}:1h",
            ))
        saved_keys = set(
            Notification.objects.filter(recipient=owner, dedupe_key__in=keys)
            .values_list("dedupe_key", flat=True)
        )
        for entry, day in batch:
            add_candidate(entry, day, saved_keys)

    dated = Entry.objects.filter(
        owner=owner, event_date__isnull=False, all_day=False
    ).filter(
        Q(event_date__gt=today) | Q(event_date=today, start_time__gt=now_time)
    ).select_related("course").order_by("event_date", "start_time", "pk")
    batch = []
    for entry in dated.iterator(chunk_size=200):
        batch.append((entry, entry.event_date))
        if len(batch) == 200:
            process_batch(batch)
            batch = []
            if len(heap) == limit:
                break
    process_batch(batch)

    recurring = Entry.objects.filter(
        owner=owner, event_date__isnull=True, weekday__isnull=False, all_day=False
    ).select_related("course").order_by("weekday", "start_time", "pk")
    batch = []
    for entry in recurring.iterator(chunk_size=200):
        days_ahead = (entry.weekday - today.weekday()) % 7
        day = today + timedelta(days=days_ahead)
        starts = timezone.make_aware(datetime.combine(day, entry.start_time), local_tz)
        if starts.astimezone(datetime_timezone.utc) <= now_utc:
            day += timedelta(days=7)
        batch.append((entry, day))
        if len(batch) == 200:
            process_batch(batch)
            batch = []
    process_batch(batch)

    result = [item for _, _, item in heap]
    return sorted(result, key=lambda item: (item["starts"].astimezone(datetime_timezone.utc), item["entry"].pk))


def time_until_label(delta):
    """Format a positive duration as a short, rounded-up time-until label."""
    seconds = max(0, delta.total_seconds())
    if seconds < 3600:
        minutes = max(1, math.ceil(seconds / 60))
        unit = "minute" if minutes == 1 else "minutes"
        return f"In {minutes} {unit}"
    if seconds < 86400:
        hours = math.ceil(seconds / 3600)
        unit = "hour" if hours == 1 else "hours"
        return f"In {hours} {unit}"
    days = max(1, int(seconds / 86400 + 0.5))
    unit = "day" if days == 1 else "days"
    return f"In {days} {unit}"
