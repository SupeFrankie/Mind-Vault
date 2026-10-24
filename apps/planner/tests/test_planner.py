from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from apps.core.testing import BaseTestCase
from apps.planner.models import Entry
from apps.planner.services import dashboard_entries, overlaps_for


class PlannerTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user("planner@x.com", "Planner User")
        self.course_unit = self.make_unit(self.user, "Planner unit")
        self.course = self.course_unit.course
        self.client.force_login(self.user)

    def form_data(self, **overrides):
        data = {
            "title": "Biology lecture", "entry_type": "class", "course": self.course.pk,
            "event_date": timezone.localdate().isoformat(), "repeat_weekly": "",
            "weekday": "", "all_day": "", "start_time": "09:00", "end_time": "10:00",
            "location": "Room 3", "notes": "Bring notes",
        }
        data.update(overrides)
        return data

    def test_login_required_for_planner_pages(self):
        self.client.logout()
        for url in ("planner:timetable", "planner:entry_create"):
            response = self.client.get(reverse(url))
            self.assertEqual(response.status_code, 302)

    def test_create_detail_edit_and_delete_entry(self):
        response = self.client.post(reverse("planner:entry_create"), self.form_data())
        entry = Entry.objects.get(owner=self.user)
        self.assertRedirects(response, reverse("planner:entry_detail", args=[entry.pk]))
        self.assertContains(self.client.get(reverse("planner:entry_detail", args=[entry.pk])), "Biology lecture")
        changed = self.form_data(title="Updated lecture")
        response = self.client.post(reverse("planner:entry_edit", args=[entry.pk]), changed)
        self.assertRedirects(response, reverse("planner:entry_detail", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Updated lecture")
        self.assertEqual(self.client.get(reverse("planner:entry_delete", args=[entry.pk])).status_code, 200)
        self.client.post(reverse("planner:entry_delete", args=[entry.pk]))
        self.assertFalse(Entry.objects.filter(pk=entry.pk).exists())

    def test_other_users_cannot_view_edit_or_delete_entry(self):
        entry = Entry.objects.create(owner=self.user, title="Private", entry_type="personal",
                                     event_date=date.today(), start_time=time(9), end_time=time(10))
        other = self.make_user("other-planner@x.com")
        self.client.force_login(other)
        for name in ("planner:entry_detail", "planner:entry_edit", "planner:entry_delete"):
            response = self.client.get(reverse(name, args=[entry.pk]))
            self.assertEqual(response.status_code, 403, name)
        self.assertEqual(self.client.post(reverse("planner:entry_delete", args=[entry.pk])).status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Private")

    def test_foreign_course_is_rejected(self):
        foreign_course = self.make_unit(self.make_user("course-owner@x.com")).course
        data = self.form_data(course=foreign_course.pk)
        response = self.client.post(reverse("planner:entry_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entry.objects.exists())

    def test_invalid_time_range_and_missing_class_course_are_rejected(self):
        response = self.client.post(reverse("planner:entry_create"), self.form_data(start_time="10:00", end_time="09:00"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entry.objects.exists())
        response = self.client.post(reverse("planner:entry_create"), self.form_data(start_time="09:00", end_time="09:00"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entry.objects.exists())
        response = self.client.post(reverse("planner:entry_create"), self.form_data(course=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entry.objects.exists())

    def test_calendar_positions_events_across_the_full_day(self):
        day = date(2026, 10, 23)
        Entry.objects.create(owner=self.user, title="Before six", entry_type="personal", event_date=day,
                             start_time=time(5, 30), end_time=time(6, 30))
        Entry.objects.create(owner=self.user, title="After eleven", entry_type="personal", event_date=day,
                             start_time=time(23, 30), end_time=time(23, 59))
        week = day - timedelta(days=day.weekday())
        response = self.client.get(reverse("planner:timetable"), {"week": week.isoformat()})
        self.assertContains(response, "Before six")
        self.assertContains(response, "top:330px;height:60px")
        self.assertContains(response, "After eleven")
        self.assertContains(response, "top:1410px")
        self.assertContains(response, "height:29px")
        self.assertEqual(list(response.context["hour_labels"]), list(range(24)))
        self.assertContains(response, 'class="planner-agenda-entry')

    def test_week_view_includes_month_and_year_boundary_dates(self):
        entry_day = date(2027, 1, 1)
        Entry.objects.create(owner=self.user, title="New year class", entry_type="personal", event_date=entry_day,
                             start_time=time(9), end_time=time(10))
        response = self.client.get(reverse("planner:timetable"), {"week": "2026-12-28"})
        self.assertContains(response, "New year class")
        self.assertContains(response, "1 Jan")

    def test_recurring_class_is_shown_each_matching_weekday(self):
        monday = date(2026, 10, 19)
        entry = Entry.objects.create(owner=self.user, title="Weekly lecture", entry_type="class", course=self.course,
                                     weekday=0, start_time=time(9), end_time=time(10))
        response = self.client.get(reverse("planner:timetable"), {"week": monday.isoformat()})
        self.assertContains(response, "Weekly lecture")
        self.assertContains(self.client.get(reverse("planner:entry_detail", args=[entry.pk])), "Every Monday")
        next_week = self.client.get(reverse("planner:timetable"), {"week": (monday + timedelta(days=7)).isoformat()})
        self.assertContains(next_week, "Weekly lecture")

    def test_weekly_repeat_form_creates_and_edits_the_full_series(self):
        data = self.form_data(title="Recurring class", event_date="", repeat_weekly="on", weekday="0")
        response = self.client.post(reverse("planner:entry_create"), data)
        entry = Entry.objects.get()
        self.assertRedirects(response, reverse("planner:entry_detail", args=[entry.pk]))
        self.assertIsNone(entry.event_date)
        self.assertEqual(entry.weekday, Entry.Weekday.MONDAY)
        changed = self.form_data(title="Updated recurring class", event_date="", repeat_weekly="on", weekday="0")
        self.client.post(reverse("planner:entry_edit", args=[entry.pk]), changed)
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Updated recurring class")
        self.assertEqual(entry.weekday, Entry.Weekday.MONDAY)

    def test_all_day_assignment_has_no_synthetic_time_and_renders_all_day(self):
        day = timezone.localdate()
        data = self.form_data(title="Essay deadline", entry_type="assignment", course="", event_date=day.isoformat(),
                              all_day="on", start_time="", end_time="")
        response = self.client.post(reverse("planner:entry_create"), data)
        entry = Entry.objects.get()
        self.assertIsNone(entry.start_time)
        self.assertIsNone(entry.end_time)
        self.assertTrue(entry.all_day)
        self.assertContains(self.client.get(reverse("planner:timetable"), {"week": (day - timedelta(days=day.weekday())).isoformat()}), "All day")

    def test_overlap_requires_acknowledgement_but_does_not_block(self):
        day = timezone.localdate()
        Entry.objects.create(owner=self.user, title="Existing", entry_type="personal", event_date=day,
                             start_time=time(9), end_time=time(10))
        data = self.form_data(title="Conflict", entry_type="study", course="", start_time="09:30", end_time="10:30")
        response = self.client.post(reverse("planner:entry_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This time overlaps")
        self.assertEqual(Entry.objects.count(), 1)
        data["confirm_overlap"] = "on"
        self.client.post(reverse("planner:entry_create"), data)
        self.assertEqual(Entry.objects.count(), 2)

    def test_recurring_overlap_checks_future_dated_occurrences(self):
        day = timezone.localdate() + timedelta(days=14)
        while day < timezone.localdate() + timedelta(days=30) and day.weekday() != 2:
            day += timedelta(days=1)
        dated = Entry.objects.create(owner=self.user, title="One-off lab", entry_type="personal", event_date=day,
                                     start_time=time(9), end_time=time(10))
        conflicts = overlaps_for(self.user, event_date=None, weekday=day.weekday(), all_day=False,
                                 start_time=time(9, 30), end_time=time(10, 30))
        self.assertEqual([entry.pk for entry in conflicts], [dated.pk])

    def test_adjacent_entries_are_not_overlaps(self):
        day = timezone.localdate() + timedelta(days=1)
        dated = Entry.objects.create(owner=self.user, title="Ends at ten", entry_type="personal", event_date=day,
                                     start_time=time(9), end_time=time(10))
        conflicts = overlaps_for(self.user, event_date=day, weekday=None, all_day=False,
                                 start_time=time(10), end_time=time(11))
        self.assertNotIn(dated, conflicts)

    def test_dated_candidate_checks_recurring_and_dated_entries(self):
        day = timezone.localdate() + timedelta(days=35)
        recurring = Entry.objects.create(owner=self.user, title="Weekly class", entry_type="class",
                                         course=self.course, weekday=day.weekday(),
                                         start_time=time(9), end_time=time(10))
        same_day = Entry.objects.create(owner=self.user, title="One-off study", entry_type="personal",
                                        event_date=day, start_time=time(9, 30), end_time=time(10, 30))
        conflicts = overlaps_for(self.user, event_date=day, weekday=None, all_day=False,
                                 start_time=time(9, 45), end_time=time(10, 15))
        self.assertCountEqual(conflicts, [recurring, same_day])

    def test_recurring_candidate_checks_recurring_conflicts_without_horizon(self):
        recurring = Entry.objects.create(owner=self.user, title="Weekly class", entry_type="class",
                                         course=self.course, weekday=4,
                                         start_time=time(9), end_time=time(10))
        conflicts = overlaps_for(self.user, event_date=None, weekday=4, all_day=False,
                                 start_time=time(9, 30), end_time=time(10, 30))
        self.assertEqual([entry.pk for entry in conflicts], [recurring.pk])

    def test_dashboard_finds_far_future_dated_event(self):
        today = date(2026, 10, 23)
        future = today + timedelta(days=100)
        entry = Entry.objects.create(owner=self.user, title="Far future exam", entry_type="assignment",
                                     event_date=future, start_time=time(9), end_time=time(10))
        local_now = timezone.make_aware(datetime.combine(today, time(8)), timezone.get_current_timezone())
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            _, _, upcoming = dashboard_entries(self.user)
        self.assertEqual(upcoming["entry"], entry)
        self.assertEqual(upcoming["date"], future)

    def test_dashboard_can_select_future_all_day_entry_without_clock_time(self):
        today = date(2026, 10, 23)
        future = today + timedelta(days=100)
        entry = Entry.objects.create(owner=self.user, title="Far future all-day deadline", entry_type="assignment",
                                     event_date=future, all_day=True)
        local_now = timezone.make_aware(datetime.combine(today, time(8)), timezone.get_current_timezone())
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            _, _, upcoming = dashboard_entries(self.user)
        self.assertEqual(upcoming["entry"], entry)
        self.assertEqual(upcoming["date"], future)
        self.assertIsNone(upcoming["starts"])

    def test_dashboard_keeps_all_day_in_agenda_but_not_as_midnight_next_up(self):
        today = date(2026, 10, 23)
        deadline = Entry.objects.create(owner=self.user, title="All-day deadline", entry_type="assignment",
                                        event_date=today, all_day=True)
        timed = Entry.objects.create(owner=self.user, title="Afternoon study", entry_type="study",
                                     event_date=today, start_time=time(13), end_time=time(14))
        local_now = timezone.make_aware(datetime.combine(today, time(12)), timezone.get_current_timezone())
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            returned_day, today_rows, upcoming = dashboard_entries(self.user)
        self.assertEqual(returned_day, today)
        self.assertIn(deadline, today_rows)
        self.assertTrue(deadline.all_day)
        self.assertEqual(upcoming["entry"], timed)
        self.assertEqual(upcoming["starts"], timezone.make_aware(
            datetime.combine(today, time(13)), timezone.get_current_timezone()
        ))

    def test_dashboard_uses_local_date_across_timezone_year_boundary(self):
        entry_day = date(2025, 12, 31)
        entry = Entry.objects.create(owner=self.user, title="Local evening class", entry_type="personal",
                                     event_date=entry_day, start_time=time(17), end_time=time(18))
        utc_now = datetime(2026, 1, 1, 0, 30, tzinfo=datetime_timezone.utc)
        with timezone.override("America/Los_Angeles"), patch("django.utils.timezone.now", return_value=utc_now):
            today, _, upcoming = dashboard_entries(self.user)
        self.assertEqual(today, entry_day)
        self.assertEqual(upcoming["entry"], entry)
        self.assertEqual(upcoming["date"], entry_day)

    def test_dashboard_skips_past_and_in_progress_events_and_handles_empty_schedule(self):
        today = date(2026, 10, 23)
        local_now = timezone.make_aware(datetime.combine(today, time(12)), timezone.get_current_timezone())
        Entry.objects.create(owner=self.user, title="Past", entry_type="personal", event_date=today,
                             start_time=time(9), end_time=time(10))
        Entry.objects.create(owner=self.user, title="In progress", entry_type="personal", event_date=today,
                             start_time=time(11), end_time=time(13))
        next_event = Entry.objects.create(owner=self.user, title="Later", entry_type="personal", event_date=today,
                                          start_time=time(14), end_time=time(15))
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            _, _, upcoming = dashboard_entries(self.user)
        self.assertEqual(upcoming["entry"], next_event)

        Entry.objects.all().delete()
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            _, today_rows, upcoming = dashboard_entries(self.user)
        self.assertEqual(today_rows, [])
        self.assertIsNone(upcoming)

    def test_dashboard_finds_next_recurring_occurrence(self):
        today = date(2026, 10, 23)  # Friday
        recurring = Entry.objects.create(owner=self.user, title="Monday class", entry_type="class", course=self.course,
                                         weekday=0, start_time=time(9), end_time=time(10))
        local_now = timezone.make_aware(datetime.combine(today, time(12)), timezone.get_current_timezone())
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            _, _, upcoming = dashboard_entries(self.user)
        self.assertEqual(upcoming["entry"], recurring)
        self.assertEqual(upcoming["date"], date(2026, 10, 26))

    def test_dashboard_surfaces_next_event_before_agenda_without_duplicate_row(self):
        today = date(2026, 10, 23)
        for hour in (8, 9, 10, 11):
            Entry.objects.create(owner=self.user, title=f"Earlier {hour}", entry_type="personal",
                                 event_date=today, start_time=time(hour), end_time=time(hour + 1))
        upcoming = Entry.objects.create(owner=self.user, title="Late seminar", entry_type="class",
                                        course=self.course, event_date=today,
                                        start_time=time(15), end_time=time(16))
        local_now = timezone.make_aware(datetime.combine(today, time(7)), timezone.get_current_timezone())
        with patch("apps.planner.services.timezone.localdate", return_value=today), \
                patch("apps.planner.services.timezone.localtime", return_value=local_now):
            response = self.client.get(reverse("core:dashboard"))
        html = response.content.decode()
        self.assertLess(html.index("Next up · timed event"), html.index("Earlier 8"))
        self.assertEqual(html.count("Late seminar"), 1)
        self.assertContains(response, "3:00 PM")
        self.assertContains(response, "Upcoming in today's agenda")

    def test_selected_day_agenda_highlights_next_timed_event_and_keeps_all_day_separate(self):
        today = date(2026, 10, 23)
        deadline = Entry.objects.create(owner=self.user, title="All-day deadline", entry_type="assignment",
                                        event_date=today, all_day=True)
        next_entry = Entry.objects.create(owner=self.user, title="Late lecture", entry_type="class",
                                          course=self.course, event_date=today,
                                          start_time=time(15), end_time=time(16))
        local_now = timezone.make_aware(datetime.combine(today, time(9)), timezone.get_current_timezone())
        with patch("apps.planner.views.timezone.localdate", return_value=today), \
                patch("apps.planner.views.timezone.localtime", return_value=local_now):
            response = self.client.get(reverse("planner:timetable"), {
                "week": (today - timedelta(days=today.weekday())).isoformat(), "day": today.isoformat(),
            })
        html = response.content.decode()
        agenda_html = html.split('class="planner-mobile-agenda"', 1)[1]
        self.assertContains(response, "Next up")
        self.assertContains(response, "Late lecture")
        self.assertContains(response, "3:00 PM")
        self.assertContains(response, "All day")
        self.assertLess(agenda_html.index("Next up"), agenda_html.index("All-day deadline"))
        self.assertIn(f'href="#planner-agenda-event-{next_entry.pk}"', html)
        self.assertIn(f'id="planner-agenda-event-{next_entry.pk}"', html)
        self.assertTrue(deadline.all_day)

    def test_type_filters_and_dashboard_are_owner_scoped(self):
        day = timezone.localdate()
        Entry.objects.create(owner=self.user, title="My class", entry_type="class", course=self.course,
                             event_date=day, start_time=time(9), end_time=time(10))
        Entry.objects.create(owner=self.user, title="My revision", entry_type="study",
                             event_date=day, start_time=time(11), end_time=time(12))
        Entry.objects.create(owner=self.user, title="Upcoming class", entry_type="class", course=self.course,
                             event_date=day + timedelta(days=30), start_time=time(9), end_time=time(10))
        stranger = self.make_user("stranger@x.com")
        Entry.objects.create(owner=stranger, title="Other private", entry_type="personal",
                             event_date=day, start_time=time(13), end_time=time(14))
        filtered = self.client.get(reverse("planner:timetable"), {"week": day.isoformat(), "type": "study"})
        self.assertContains(filtered, "My revision")
        self.assertNotContains(filtered, "My class")
        dashboard = self.client.get(reverse("core:dashboard"))
        self.assertContains(dashboard, "My class")
        self.assertContains(dashboard, "Upcoming class")
        self.assertContains(dashboard, "Next up")
        self.assertNotContains(dashboard, "Other private")
