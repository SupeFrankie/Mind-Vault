from datetime import date, datetime, time, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.urls import reverse

from apps.core.models import Notification
from apps.core.testing import BaseTestCase
from apps.planner.models import Entry
from apps.planner.services import time_until_label, upcoming_events


class ReminderCommandTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user("reminders@example.com")
        self.tz = ZoneInfo("Africa/Nairobi")

    def run_at(self, instant):
        with patch("django.utils.timezone.now", return_value=instant):
            call_command("send_event_reminders", stdout=StringIO())

    def make_entry(self, day, start=time(10), **kwargs):
        defaults = dict(owner=self.user, title="Database Systems", entry_type=Entry.Type.CLASS,
                        event_date=day, start_time=start, end_time=time(start.hour + 1, start.minute))
        defaults.update(kwargs)
        return Entry.objects.create(**defaults)

    def test_one_hour_reminder_is_created_at_the_due_window(self):
        day = date(2026, 11, 12)
        entry = self.make_entry(day, start=time(10))
        self.run_at(datetime(2026, 11, 12, 9, 0, tzinfo=self.tz))
        notification = Notification.objects.get()
        self.assertEqual(notification.message, "Database Systems in 1 hour.")
        self.assertEqual(notification.recipient, self.user)
        self.assertEqual(notification.target_url, reverse("planner:entry_detail", args=[entry.pk]))

    def test_future_dated_entry_gets_24_hour_reminder(self):
        day = date(2026, 11, 12)
        self.make_entry(day, start=time(10), entry_type=Entry.Type.PERSONAL)
        self.run_at(datetime(2026, 11, 11, 10, 0, tzinfo=self.tz))
        self.assertEqual(Notification.objects.get().message, "Database Systems in 24 hours.")

    def test_repeated_command_does_not_duplicate_reminder(self):
        self.make_entry(date(2026, 11, 12))
        instant = datetime(2026, 11, 12, 9, 0, tzinfo=self.tz)
        self.run_at(instant)
        self.run_at(instant)
        self.assertEqual(Notification.objects.count(), 1)

    def test_all_day_entry_does_not_get_clock_reminder(self):
        Entry.objects.create(owner=self.user, title="All-day deadline", entry_type=Entry.Type.ASSIGNMENT,
                             event_date=date(2026, 11, 12), all_day=True)
        self.run_at(datetime(2026, 11, 12, 9, 0, tzinfo=self.tz))
        self.assertFalse(Notification.objects.exists())

    def test_recurring_occurrence_gets_reminder_and_owner_is_recipient(self):
        day = date(2026, 11, 12)  # Thursday
        other = self.make_user("other-reminder@example.com")
        Entry.objects.create(owner=self.user, title="Weekly class", entry_type=Entry.Type.CLASS,
                             weekday=day.weekday(), start_time=time(10), end_time=time(11))
        Entry.objects.create(owner=other, title="Other user's event", entry_type=Entry.Type.PERSONAL,
                             event_date=day, start_time=time(10), end_time=time(11))
        self.run_at(datetime(2026, 11, 12, 9, 0, tzinfo=self.tz))
        self.assertEqual(Notification.objects.count(), 2)
        self.assertSetEqual(
            set(Notification.objects.values_list("recipient_id", flat=True)),
            {self.user.pk, other.pk},
        )
        self.assertTrue(Notification.objects.filter(recipient=self.user, message="Weekly class in 1 hour.").exists())


class NotificationAccessTests(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("notification-owner@example.com")
        self.other = self.make_user("notification-other@example.com")
        self.notification = Notification.objects.create(
            recipient=self.owner, message="Database Systems in 1 hour.", target_url="/timetable/",
            dedupe_key="entry:1:2026-11-12:1h",
        )

    def test_notification_list_requires_login_and_is_user_scoped(self):
        url = reverse("core:notifications")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.other)
        self.assertNotContains(self.client.get(url), "Database Systems")
        self.login(self.owner)
        self.assertContains(self.client.get(url), "Database Systems")

    def test_authenticated_top_bar_bell_links_to_inbox_and_shows_unread_count(self):
        self.login(self.owner)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'class="notification-bell"')
        self.assertContains(response, 'href="/notifications/"')
        self.assertContains(response, 'class="notification-bell-count"')
        self.assertContains(response, ">1</span>")

    def test_top_bar_bell_has_no_badge_when_all_notifications_are_read(self):
        self.notification.read_at = self.notification.created_at
        self.notification.save(update_fields=["read_at"])
        self.login(self.owner)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'class="notification-bell"')
        self.assertNotContains(response, 'class="notification-bell-count"')

    def test_top_bar_unread_badge_caps_at_99_plus(self):
        Notification.objects.bulk_create([
            Notification(recipient=self.owner, message="Reminder", target_url="/notifications/",
                         dedupe_key=f"bulk-reminder:{index}")
            for index in range(100)
        ])
        self.login(self.owner)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'aria-label="Notifications, 99+ unread"')
        self.assertContains(response, ">99+</span>")

    def test_only_recipient_can_mark_notification_read(self):
        url = reverse("core:notification_mark_read", args=[self.notification.pk])
        self.login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.notification.refresh_from_db()
        self.assertIsNone(self.notification.read_at)
        self.login(self.owner)
        self.assertRedirects(self.client.post(url), reverse("core:notifications"))
        self.notification.refresh_from_db()
        self.assertIsNotNone(self.notification.read_at)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), 'class="notification-bell-count"')


class UpcomingEventTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user("upcoming@example.com")
        self.other = self.make_user("other-upcoming@example.com")
        self.tz = ZoneInfo("Africa/Nairobi")

    def add_entry(self, owner, day, title, start, end=None, **kwargs):
        return Entry.objects.create(
            owner=owner,
            title=title,
            entry_type=kwargs.pop("entry_type", Entry.Type.PERSONAL),
            event_date=day,
            start_time=start,
            end_time=end or time((start.hour + 1) % 24, start.minute),
            **kwargs,
        )

    def test_future_events_are_sorted_and_past_in_progress_and_all_day_are_excluded(self):
        now = datetime(2026, 12, 31, 8, 0, tzinfo=self.tz)
        today = now.date()
        self.add_entry(self.user, today, "Past", time(6), time(7))
        self.add_entry(self.user, today, "In progress", time(7), time(9))
        self.add_entry(self.user, today, "Later today", time(10), time(11))
        self.add_entry(self.user, date(2027, 1, 3), "After New Year", time(11), time(12))
        Entry.objects.create(owner=self.user, title="All-day", entry_type=Entry.Type.ASSIGNMENT,
                             event_date=date(2027, 1, 1), all_day=True)
        self.add_entry(self.other, today, "Another user's event", time(9), time(10))

        events = upcoming_events(self.user, now=now)

        self.assertEqual([item["entry"].title for item in events], ["Later today", "After New Year"])
        self.assertEqual(events[0]["time_until"], "In 2 hours")
        self.assertEqual(events[1]["time_until"], "In 3 days")

    def test_recurring_event_uses_next_local_weekday_occurrence(self):
        now = datetime(2026, 12, 31, 10, 0, tzinfo=self.tz)  # Thursday
        entry = Entry.objects.create(owner=self.user, title="Weekly lecture", entry_type=Entry.Type.CLASS,
                                     weekday=3, start_time=time(9), end_time=time(10))

        events = upcoming_events(self.user, now=now)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["entry"], entry)
        self.assertEqual(events[0]["date"], date(2027, 1, 7))
        self.assertEqual(events[0]["starts"].tzinfo.key, "Africa/Nairobi")

    def test_occurrence_with_saved_reminder_is_not_repeated_in_upcoming(self):
        now = datetime(2026, 10, 23, 8, 0, tzinfo=self.tz)
        entry = self.add_entry(self.user, now.date(), "Database Systems", time(10), time(11),
                               entry_type=Entry.Type.ASSIGNMENT)
        notification = Notification.objects.create(
            recipient=self.user,
            message="Assignment deadline: Database Systems in 1 hour.",
            target_url=f"/timetable/{entry.pk}/",
            dedupe_key=f"planner-entry:{entry.pk}:{now.date().isoformat()}:10:00:00:1h",
        )
        notification.created_at = now - timedelta(days=10)
        notification.save(update_fields=["created_at"])

        self.assertEqual(upcoming_events(self.user, now=now), [])

    def test_time_until_labels_are_clear_and_rounded(self):
        self.assertEqual(time_until_label(timedelta(hours=2)), "In 2 hours")
        self.assertEqual(time_until_label(timedelta(days=3)), "In 3 days")
        self.assertEqual(time_until_label(timedelta(seconds=30)), "In 1 minute")

    def test_notifications_inbox_renders_upcoming_section(self):
        now = datetime(2026, 10, 23, 8, 0, tzinfo=self.tz)
        self.add_entry(self.user, date(2026, 10, 26), "Study group", time(11), time(12))
        self.login(self.user)
        with patch("django.utils.timezone.now", return_value=now):
            response = self.client.get(reverse("core:notifications"))
        self.assertContains(response, "Upcoming")
        self.assertContains(response, "Study group")
        self.assertContains(response, "In 3 days")
        self.assertContains(response, "Event reminders and study-group activity.")
