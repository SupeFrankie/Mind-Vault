import os
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from .models import Course, Material, Semester, Tag, Unit
from .services import parse_tags
from .validators import validate_upload


class _OwnedNameForm(forms.ModelForm):
    """Enforces per-parent name uniqueness. ModelForm skips UniqueConstraints that involve
    fields not on the form (owner/semester/course), so we check explicitly; the DB
    constraint remains the final guard."""

    name_field = "title"

    def _check_unique(self, scope):
        value = (self.cleaned_data.get(self.name_field) or "").strip()
        qs = type(self.instance).objects.filter(**scope, **{f"{self.name_field}__iexact": value})
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("You already have one with this name here.")
        return value


class SemesterForm(_OwnedNameForm):
    name_field = "name"

    class Meta:
        model = Semester
        fields = ["name"]

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_name(self):
        return self._check_unique({"owner": self.owner})

    def save(self, commit=True):
        self.instance.owner = self.owner
        return super().save(commit)


class CourseForm(_OwnedNameForm):
    class Meta:
        model = Course
        fields = ["code", "title"]

    def __init__(self, *args, semester, **kwargs):
        super().__init__(*args, **kwargs)
        self.semester = semester

    def clean_title(self):
        return self._check_unique({"semester": self.semester})

    def save(self, commit=True):
        self.instance.semester = self.semester
        return super().save(commit)


class UnitForm(_OwnedNameForm):
    class Meta:
        model = Unit
        fields = ["title", "order"]

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course

    def clean_title(self):
        return self._check_unique({"course": self.course})

    def save(self, commit=True):
        self.instance.course = self.course
        return super().save(commit)


class MaterialForm(forms.ModelForm):
    """Owner-only create/edit. `unit` choices are limited to the owner's own units,
    so a crafted POST with someone else's unit id fails validation (no IDOR)."""

    tag_text = forms.CharField(
        required=False, label="Tags", help_text="Comma separated, e.g. exam, week 3", max_length=300
    )

    class Meta:
        model = Material
        fields = ["unit", "title", "kind", "file", "notes", "authors", "publication_year", "doi"]

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self._content_type = ""
        self._old_file_name = self.instance.file.name if self.instance.pk and self.instance.file else ""
        self.fields["unit"].queryset = Unit.objects.filter(course__semester__owner=owner).select_related(
            "course__semester"
        )
        self.fields["unit"].label_from_instance = lambda u: f"{u.course.semester.name} / {u.course.title} / {u.title}"
        if self.instance.pk:
            self.fields["tag_text"].initial = ", ".join(t.name for t in self.instance.tags.all())

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if isinstance(f, UploadedFile):  # a new upload (not the unchanged stored file, not "clear")
            self._content_type = validate_upload(f)
        return f

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise ValidationError("Title is required.")
        return title

    def clean_publication_year(self):
        year = self.cleaned_data.get("publication_year")
        if year is not None and not (1800 <= year <= date.today().year + 1):
            raise ValidationError("Enter a realistic publication year.")
        return year

    def clean_doi(self):
        doi = (self.cleaned_data.get("doi") or "").strip()
        if doi and not doi.lower().startswith("10."):
            raise ValidationError("A DOI starts with '10.' (e.g. 10.1000/xyz123).")
        return doi

    def clean_tag_text(self):
        tags = parse_tags(self.cleaned_data.get("tag_text", ""))
        if len(tags) > 10 or any(len(t) > 30 for t in tags):
            raise ValidationError("Use at most 10 tags, each up to 30 characters.")
        return ", ".join(tags)

    def clean(self):
        cleaned = super().clean()
        has_file = bool(cleaned.get("file"))
        if not has_file and not (cleaned.get("notes") or "").strip():
            raise ValidationError("Add a file, some text notes, or both.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        material = super().save(commit=False)
        material.owner = self.owner
        new_file = self.cleaned_data.get("file")
        if isinstance(new_file, UploadedFile):
            material.original_filename = os.path.basename(new_file.name)[:255]
            material.content_type = self._content_type
            material.file_size = new_file.size
        elif not new_file:  # cleared or never had one
            material.original_filename = material.content_type = ""
            material.file_size = 0
        material.save()
        names = parse_tags(self.cleaned_data.get("tag_text", ""))
        tags = [Tag.objects.get_or_create(owner=self.owner, name=n)[0] for n in names]
        material.tags.set(tags)
        if self._old_file_name and self._old_file_name != material.file.name:
            storage, old = material.file.storage, self._old_file_name
            transaction.on_commit(lambda: storage.delete(old))
        return material
