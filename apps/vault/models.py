import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def material_upload_path(instance, filename):
    """Random storage name: the user-supplied filename never touches the filesystem path.
    The original name is kept in Material.original_filename for display/download only."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"materials/{uuid.uuid4().hex}.{ext}"


class Semester(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="semesters")
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="uniq_semester_owner_name")]

    def __str__(self):
        return self.name


class Course(models.Model):
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="courses")
    code = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        constraints = [models.UniqueConstraint(fields=["semester", "title"], name="uniq_course_semester_title")]

    @property
    def owner(self):
        return self.semester.owner

    @property
    def owner_id(self):
        return self.semester.owner_id

    def __str__(self):
        return f"{self.code} {self.title}".strip()


class Unit(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="units")
    title = models.CharField(max_length=150)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]
        constraints = [models.UniqueConstraint(fields=["course", "title"], name="uniq_unit_course_title")]

    @property
    def owner(self):
        return self.course.semester.owner

    @property
    def owner_id(self):
        return self.course.semester.owner_id

    def __str__(self):
        return self.title


class Tag(models.Model):
    """Tags are per-owner so one student's tags never appear in another's pickers."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=30)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="uniq_tag_owner_name")]

    def __str__(self):
        return self.name


class Material(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note", "Lecture note"
        SLIDES = "slides", "Lecture slides"
        ASSIGNMENT = "assignment", "Assignment"
        PAST_PAPER = "past_paper", "Past paper"
        RESEARCH = "research_paper", "Research paper"
        OTHER = "other", "Other"

    # Deletion: removing a unit/course/semester/user deletes its materials (and, via Share's
    # CASCADE, their shares). Audit history survives because AuditEvent has no FK to Material.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="materials")
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="materials")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.NOTE)
    file = models.FileField(upload_to=material_upload_path, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)  # assigned by server, see validators
    file_size = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="materials")
    # Optional research-paper metadata (no discovery/crawler: the student types it in).
    authors = models.CharField(max_length=300, blank=True)
    publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    doi = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "unit"]),
            models.Index(fields=["owner", "kind"]),
            models.Index(fields=["title"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""), name="material_title_not_empty"
            ),
        ]

    @property
    def course(self):
        return self.unit.course

    @property
    def has_file(self):
        return bool(self.file)

    def __str__(self):
        return self.title
