from django import forms
from django.core.exceptions import ValidationError

from apps.vault.models import Course

from .models import Entry


class EntryForm(forms.ModelForm):
    repeat_weekly = forms.BooleanField(required=False, label="Repeat every week")

    class Meta:
        model = Entry
        exclude = ["owner", "created_at", "updated_at"]
        widgets = {
            "event_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["course"].queryset = Course.objects.filter(semester__owner=owner).select_related("semester")
        self.fields["course"].required = False
        self.fields["event_date"].required = False
        self.fields["weekday"].required = False
        self.fields["weekday"].choices = [("", "Choose a weekday")] + list(Entry.Weekday.choices)
        self.fields["weekday"].widget.attrs["data-weekday-field"] = ""
        self.fields["repeat_weekly"].widget.attrs["data-repeat-toggle"] = ""
        self.fields["entry_type"].widget.attrs["data-entry-type"] = ""
        self.fields["all_day"].widget.attrs["data-all-day-toggle"] = ""
        self.fields["start_time"].widget.attrs["data-time-field"] = ""
        self.fields["end_time"].widget.attrs["data-time-field"] = ""
        self.fields["course"].widget.attrs["data-course-field"] = ""
        if self.instance.pk:
            self.initial["repeat_weekly"] = self.instance.weekday is not None

    def clean(self):
        cleaned = super().clean()
        entry_type = cleaned.get("entry_type")
        course = cleaned.get("course")
        repeats = cleaned.get("repeat_weekly")
        event_date = cleaned.get("event_date")
        weekday = cleaned.get("weekday")
        all_day = cleaned.get("all_day")
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")

        if entry_type == Entry.Type.CLASS and not course:
            self.add_error("course", "Choose a course for a class.")
        elif entry_type == Entry.Type.PERSONAL and course:
            self.add_error("course", "Personal events cannot be linked to a course.")
        if repeats:
            if entry_type != Entry.Type.CLASS:
                self.add_error("repeat_weekly", "Only classes can repeat weekly.")
            if weekday is None:
                self.add_error("weekday", "Choose the recurring weekday.")
            cleaned["event_date"] = None
        else:
            if not event_date:
                self.add_error("event_date", "Choose the event date.")
            cleaned["weekday"] = None
        if all_day:
            if entry_type != Entry.Type.ASSIGNMENT:
                self.add_error("all_day", "Only assignments can be all-day.")
            if repeats:
                self.add_error("all_day", "All-day assignments must have a specific date.")
            cleaned["start_time"] = None
            cleaned["end_time"] = None
        elif start is None or end is None:
            self.add_error("start_time", "Enter both times, or mark the assignment all-day.")
        elif end <= start:
            self.add_error("end_time", "End time must be later than start time.")
        return cleaned

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.owner = self.owner
        if commit:
            entry.full_clean()
            entry.save()
        return entry
