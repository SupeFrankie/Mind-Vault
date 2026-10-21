import os
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.forms.models import construct_instance

from .models import Course, Material, Semester, Tag, Unit
from .services import parse_tags
from .sensitive_data import scan_pdf
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
        fields = ["unit", "title", "kind", "sensitivity", "file", "notes", "authors", "publication_year", "doi"]

    def __init__(self, *args, owner, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.request = request
        self._content_type = ""
        self._sensitive_data_scan = None
        self.fields["unit"].queryset = Unit.objects.filter(course__semester__owner=owner).select_related(
            "course__semester"
        )
        self.fields["unit"].label_from_instance = lambda u: f"{u.course.semester.name} / {u.course.title} / {u.title}"
        # Existing records remain unclassified until their owner explicitly chooses a level.
        self.fields["sensitivity"].required = True
        if self.instance.pk:
            self.fields["tag_text"].initial = ", ".join(t.name for t in self.instance.tags.all())

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if isinstance(f, UploadedFile):  # a new upload (not the unchanged stored file, not "clear")
            self._content_type = validate_upload(f)
            self._sensitive_data_scan = scan_pdf(f) if self._content_type == "application/pdf" else {
                "status": "not_applicable", "findings": []
            }
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
        old_sensitivity = None
        editing_existing = bool(self.instance.pk)
        old_file_name = ""
        if editing_existing:
            # Serialize classification changes against share creation/update, which
            # locks the same material row before it evaluates policy.
            self.instance = Material.objects.select_for_update().get(pk=self.instance.pk)
            old_sensitivity = self.instance.sensitivity
            old_file_name = self.instance.file.name if self.instance.file else ""
            if self.request is not None and self.request.user.pk != self.owner.pk:
                raise ValidationError("Only the material owner can change this material.")
            # ModelForm validation populated the prior instance. Reapply only the
            # validated model fields to the locked row, keeping an omitted file
            # from the freshly locked row so a concurrent replacement is not undone.
            construct_instance(
                self,
                self.instance,
                fields=[name for name in self._meta.fields if name != "file"],
                exclude=self._meta.exclude,
            )
            new_file = self.cleaned_data.get("file")
            if isinstance(new_file, UploadedFile) or new_file is False:
                self.instance.file = new_file
                if new_file is False:
                    self.instance.sensitive_data_scan = {"status": "not_applicable", "findings": []}
                elif self._sensitive_data_scan is not None:
                    self.instance.sensitive_data_scan = self._sensitive_data_scan
        material = super().save(commit=False)
        material.owner = self.owner
        new_file = self.cleaned_data.get("file")
        if not editing_existing and isinstance(new_file, UploadedFile):
            material.sensitive_data_scan = self._sensitive_data_scan or {
                "status": "not_applicable", "findings": []
            }
        try:
            if isinstance(new_file, UploadedFile):
                material.original_filename = os.path.basename(new_file.name)[:255]
                material.content_type = self._content_type
                material.file_size = new_file.size
            elif new_file is False:  # explicitly cleared
                material.original_filename = material.content_type = ""
                material.file_size = 0
            material.save()
            names = parse_tags(self.cleaned_data.get("tag_text", ""))
            tags = [Tag.objects.get_or_create(owner=self.owner, name=n)[0] for n in names]
            material.tags.set(tags)
            if editing_existing and old_sensitivity != material.sensitivity:
                if self.request is None:
                    raise ValidationError("A request is required to record a sensitivity change.")
                from apps.sharing.services import reconcile_sensitivity_change

                reconcile_sensitivity_change(self.request, material, old_sensitivity)
            if old_file_name and old_file_name != material.file.name:
                storage, old = material.file.storage, old_file_name
                transaction.on_commit(lambda: storage.delete(old))
        except BaseException:
            # File storage is not transactional: Model.save() can store the upload
            # before later database/audit work fails. Remove that uncommitted file;
            # the prior file remains because its deletion is registered on commit.
            if isinstance(new_file, UploadedFile):
                replacement_name = material.file.name if material.file else ""
                if replacement_name and replacement_name != old_file_name:
                    material.file.storage.delete(replacement_name)
            raise
        return material
