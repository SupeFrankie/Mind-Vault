from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.vault.models import Course


class Entry(models.Model):
    class Type(models.TextChoices):
        CLASS = "class", "Class"
        STUDY = "study", "Study"
        ASSIGNMENT = "assignment", "Assignment"
        PERSONAL = "personal", "Personal"

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="planner_entries")
    title = models.CharField(max_length=160)
    entry_type = models.CharField(max_length=12, choices=Type.choices)
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE, related_name="planner_entries")
    event_date = models.DateField(null=True, blank=True, db_index=True)
    weekday = models.PositiveSmallIntegerField(null=True, blank=True, choices=Weekday.choices)
    all_day = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event_date", "weekday", "start_time", "title"]
        indexes = [models.Index(fields=["owner", "weekday"])]
        constraints = [
            models.CheckConstraint(
                condition=(Q(event_date__isnull=False, weekday__isnull=True) | Q(event_date__isnull=True, weekday__isnull=False)),
                name="planner_entry_date_or_weekday",
            ),
            models.CheckConstraint(
                condition=(Q(all_day=True, start_time__isnull=True, end_time__isnull=True) |
                           Q(all_day=False, start_time__isnull=False, end_time__isnull=False, end_time__gt=F("start_time"))),
                name="planner_entry_valid_times",
            ),
            models.CheckConstraint(condition=Q(weekday__isnull=True) | Q(weekday__lte=6), name="planner_entry_valid_weekday"),
        ]

    def clean(self):
        errors = {}
        if self.entry_type == self.Type.CLASS and not self.course_id:
            errors["course"] = "Choose a course for a class."
        if self.entry_type == self.Type.PERSONAL and self.course_id:
            errors["course"] = "Personal events cannot be linked to a course."
        if self.weekday is not None and self.entry_type != self.Type.CLASS:
            errors["weekday"] = "Only classes can repeat weekly."
        if self.all_day and (self.entry_type != self.Type.ASSIGNMENT or self.weekday is not None):
            errors["all_day"] = "Only dated assignments can be all-day."
        if self.all_day:
            if self.start_time is not None or self.end_time is not None:
                errors["all_day"] = "All-day entries must not have start or end times."
        elif self.start_time is None or self.end_time is None:
            errors["start_time"] = "Enter both a start and end time, or mark the assignment all-day."
        elif self.end_time <= self.start_time:
            errors["end_time"] = "End time must be later than start time."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title
