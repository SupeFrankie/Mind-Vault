"""Shared test helpers. Uses a temporary MEDIA_ROOT so tests never touch real uploads."""
import io
import shutil
import tempfile
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.vault.models import Course, Material, Semester, Unit

_TMP = tempfile.mkdtemp(prefix="mindvault-test-")
PASSWORD = "a-Strong-pass-123"


def pdf_file(name="notes.pdf", size=100):
    return SimpleUploadedFile(name, b"%PDF-1.4\n" + b"x" * size, content_type="application/pdf")


def docx_file(name="essay.docx"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    return SimpleUploadedFile(name, buf.getvalue())


@override_settings(MEDIA_ROOT=_TMP)
class BaseTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP, ignore_errors=True)

    def make_user(self, email, name="Test User"):
        return get_user_model().objects.create_user(email=email, password=PASSWORD, full_name=name)

    def login(self, user):
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def make_unit(self, owner, title="Unit 1"):
        sem = Semester.objects.get_or_create(owner=owner, name="Sem 1")[0]
        course = Course.objects.get_or_create(semester=sem, title="Digital Logic", defaults={"code": "CS101"})[0]
        return Unit.objects.create(course=course, title=title)

    def make_material(self, owner, title="Notes", unit=None, with_file=False, **kw):
        unit = unit or self.make_unit(owner, title=f"U-{title}")
        m = Material.objects.create(owner=owner, unit=unit, title=title, notes=kw.pop("notes", "some notes"), **kw)
        if with_file:
            m.file.save("x.pdf", pdf_file(), save=False)
            m.original_filename, m.content_type, m.file_size = "lecture.pdf", "application/pdf", 109
            m.save()
        return m
