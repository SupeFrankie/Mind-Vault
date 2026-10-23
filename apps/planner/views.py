from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core import audit

from .access import get_owned_entry
from .forms import EntryForm
from .models import Entry
from .services import dashboard_entries, overlaps_for


def _monday(value):
    return value - timedelta(days=value.weekday())


@login_required
def timetable(request):
    raw_week = request.GET.get("week", "")
    try:
        week = _monday(date.fromisoformat(raw_week)) if raw_week else _monday(timezone.localdate())
    except (TypeError, ValueError):
        week = _monday(timezone.localdate())
    days = [week + timedelta(days=n) for n in range(7)]
    selected_raw = request.GET.get("day", "")
    try:
        selected_day = date.fromisoformat(selected_raw)
    except (TypeError, ValueError):
        selected_day = timezone.localdate()
    if selected_day not in days:
        selected_day = days[0]

    chosen_types = [value for value in request.GET.getlist("type") if value in Entry.Type.values]
    qs = Entry.objects.filter(owner=request.user).filter(
        Q(event_date__range=(days[0], days[-1])) |
        Q(event_date__isnull=True, weekday__isnull=False)
    ).select_related("course")
    events = list(qs)
    if chosen_types:
        events = [entry for entry in events if entry.entry_type in chosen_types]

    days_data = []
    for day in days:
        rows = [entry for entry in events if entry.event_date == day or entry.weekday == day.weekday()]
        rows.sort(key=lambda entry: (entry.all_day is False, entry.start_time or timezone.datetime.min.time(), entry.title.casefold()))
        days_data.append({"date": day, "entries": rows})
    current_time = timezone.localtime().time().replace(tzinfo=None)
    focus_entry = None
    focus_state = None
    if selected_day >= timezone.localdate():
        selected_rows = next(day_data["entries"] for day_data in days_data if day_data["date"] == selected_day)
        timed_rows = [entry for entry in selected_rows if not entry.all_day]
        if selected_day == timezone.localdate():
            focus_entry = next((entry for entry in timed_rows
                                if entry.start_time <= current_time < entry.end_time), None)
            if focus_entry is not None:
                focus_state = "current"
            else:
                focus_entry = next((entry for entry in timed_rows if entry.start_time > current_time), None)
                if focus_entry is not None:
                    focus_state = "next"
        else:
            focus_entry = timed_rows[0] if timed_rows else None
            if focus_entry is not None:
                focus_state = "next"
    if focus_entry is not None:
        focus_entry.agenda_focus_state = focus_state
    # Entries are constrained to one calendar day by the form and database
    # checks, so the grid can represent every valid TimeField value directly.
    hour_labels = range(24)
    for day_data in days_data:
        positioned = []
        all_day_entries = []
        for entry in day_data["entries"]:
            if entry.all_day:
                all_day_entries.append(entry)
                continue
            start_minutes = entry.start_time.hour * 60 + entry.start_time.minute
            end_minutes = entry.end_time.hour * 60 + entry.end_time.minute
            top = start_minutes
            bottom = end_minutes
            positioned.append({"entry": entry, "top": top, "height": bottom - top,
                               "start_label": entry.start_time.strftime("%I:%M %p").lstrip("0"),
                               "end_position": bottom})
        lane_ends = []
        for item in positioned:
            start_position = item["top"]
            lane = next((index for index, end_position in enumerate(lane_ends)
                         if end_position <= start_position), len(lane_ends))
            if lane == len(lane_ends):
                lane_ends.append(item["end_position"])
            else:
                lane_ends[lane] = item["end_position"]
            item["lane"] = lane
        lane_count = max(1, len(lane_ends))
        for item in positioned:
            item["lane_left"] = item["lane"] * 100 / lane_count
            item["lane_width"] = 100 / lane_count
        day_data["positioned"] = positioned
        day_data["all_day_entries"] = all_day_entries
    def week_url(target):
        params = [("week", target.isoformat())]
        params.extend(("type", value) for value in chosen_types)
        return f"?{urlencode(params)}"

    return render(request, "planner/timetable.html", {
        "week": week, "week_end": days[-1], "days": days_data, "selected_day": selected_day,
        "planner_today": timezone.localdate(),
        "previous_week_url": week_url(week - timedelta(days=7)),
        "next_week_url": week_url(week + timedelta(days=7)),
        "today_url": week_url(_monday(timezone.localdate())),
        "selected_types": chosen_types, "entry_types": Entry.Type.choices,
        "hour_labels": hour_labels,
        "selected_day_focus": focus_entry,
        "selected_day_focus_state": focus_state,
    })


@login_required
def entry_create(request):
    form = EntryForm(request.POST or None, owner=request.user)
    conflicts = []
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        conflicts = overlaps_for(request.user, event_date=data["event_date"], weekday=data["weekday"],
                                 all_day=data["all_day"], start_time=data["start_time"], end_time=data["end_time"])
        if conflicts and request.POST.get("confirm_overlap") != "on":
            return render(request, "planner/form.html", {
                "form": form, "title": "Add timetable entry", "submit": "Save entry",
                "cancel_url": "planner:timetable", "conflicts": conflicts,
            })
        entry = form.save()
        messages.success(request, f"'{entry.title}' added to your timetable.")
        return redirect("planner:entry_detail", pk=entry.pk)
    return render(request, "planner/form.html", {
        "form": form, "title": "Add timetable entry", "submit": "Save entry",
        "cancel_url": "planner:timetable", "conflicts": conflicts,
    })


@login_required
def entry_detail(request, pk):
    entry = get_owned_entry(request, pk)
    return render(request, "planner/detail.html", {"entry": entry})


@login_required
def entry_edit(request, pk):
    entry = get_owned_entry(request, pk)
    form = EntryForm(request.POST or None, instance=entry, owner=request.user)
    conflicts = []
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        conflicts = overlaps_for(request.user, event_date=data["event_date"], weekday=data["weekday"],
                                 all_day=data["all_day"], start_time=data["start_time"], end_time=data["end_time"],
                                 exclude_pk=entry.pk)
        if conflicts and request.POST.get("confirm_overlap") != "on":
            return render(request, "planner/form.html", {
                "form": form, "title": "Edit timetable entry", "submit": "Save changes",
                "cancel_url": "planner:entry_detail", "cancel_pk": entry.pk, "conflicts": conflicts,
            })
        updated = form.save()
        messages.success(request, f"'{updated.title}' updated.")
        return redirect("planner:entry_detail", pk=entry.pk)
    return render(request, "planner/form.html", {
        "form": form, "title": "Edit timetable entry", "submit": "Save changes",
        "cancel_url": "planner:entry_detail", "cancel_pk": entry.pk, "conflicts": conflicts,
    })


@login_required
def entry_delete(request, pk):
    entry = get_owned_entry(request, pk)
    if request.method == "POST":
        audit.record("planner.entry.delete", "allowed", request=request, target=entry,
                     title=entry.title, entry_type=entry.entry_type)
        entry.delete()
        messages.success(request, "Timetable entry deleted.")
        return redirect("planner:timetable")
    return render(request, "planner/confirm_delete.html", {"entry": entry})


