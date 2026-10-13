from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase, docx_file, pdf_file
from apps.vault.models import Course, Material, Semester, Unit
from apps.vault.validators import validate_upload


class HierarchyTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user("a@x.com")
        self.login(self.user)

    def test_create_edit_semester_course_unit(self):
        r = self.client.post(reverse("vault:semester_create"), {"name": "Semester 1"})
        sem = Semester.objects.get(owner=self.user)
        self.assertRedirects(r, reverse("vault:semester_detail", args=[sem.pk]))
        self.client.post(reverse("vault:semester_edit", args=[sem.pk]), {"name": "Sem One"})
        sem.refresh_from_db()
        self.assertEqual(sem.name, "Sem One")
        self.client.post(reverse("vault:course_create", args=[sem.pk]), {"code": "CS1", "title": "Logic"})
        course = Course.objects.get(semester=sem)
        self.client.post(reverse("vault:unit_create", args=[course.pk]), {"title": "Gates", "order": 1})
        self.assertTrue(Unit.objects.filter(course=course, title="Gates").exists())

    def test_duplicate_names_rejected_by_form_and_by_database(self):
        Semester.objects.create(owner=self.user, name="S1")
        r = self.client.post(reverse("vault:semester_create"), {"name": "s1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Semester.objects.filter(owner=self.user).count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Semester.objects.create(owner=self.user, name="S1")

    def test_other_user_can_reuse_a_semester_name(self):
        other = self.make_user("b@x.com")
        Semester.objects.create(owner=other, name="S1")
        self.client.post(reverse("vault:semester_create"), {"name": "S1"})
        self.assertTrue(Semester.objects.filter(owner=self.user, name="S1").exists())

    def test_cannot_touch_another_users_hierarchy(self):
        other = self.make_user("b@x.com")
        unit = self.make_unit(other)
        course, sem = unit.course, unit.course.semester
        for name, arg in (("vault:semester_detail", sem.pk), ("vault:semester_edit", sem.pk),
                          ("vault:semester_delete", sem.pk), ("vault:course_detail", course.pk),
                          ("vault:unit_detail", unit.pk), ("vault:unit_delete", unit.pk)):
            self.assertEqual(self.client.get(reverse(name, args=[arg])).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("vault:semester_delete", args=[sem.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("vault:course_create", args=[sem.pk]),
                                          {"title": "Injected"}).status_code, 403)
        self.assertTrue(Semester.objects.filter(pk=sem.pk).exists())
        self.assertFalse(Course.objects.filter(title="Injected").exists())

    def test_missing_ids_return_404(self):
        self.assertEqual(self.client.get(reverse("vault:semester_detail", args=[99999])).status_code, 404)

    def test_deleting_semester_cascades_to_materials(self):
        m = self.make_material(self.user)
        self.client.post(reverse("vault:semester_delete", args=[m.unit.course.semester_id]))
        self.assertFalse(Material.objects.filter(pk=m.pk).exists())


class MaterialTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user("a@x.com")
        self.unit = self.make_unit(self.user)
        self.login(self.user)

    def _post(self, **extra):
        data = {"unit": self.unit.pk, "title": "Week 1", "kind": "note", "notes": "hello",
                "tag_text": "Exam, exam, week 1"}
        data.update(extra)
        return self.client.post(reverse("vault:material_create"), data)

    def test_text_only_material_with_tags(self):
        r = self._post()
        m = Material.objects.get(owner=self.user)
        self.assertRedirects(r, reverse("vault:material_detail", args=[m.pk]))
        self.assertEqual(sorted(t.name for t in m.tags.all()), ["exam", "week 1"])  # normalised + de-duplicated

    def test_material_needs_file_or_notes(self):
        r = self._post(notes="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Material.objects.count(), 0)

    def test_upload_stores_random_name_and_server_content_type(self):
        self._post(file=pdf_file("../../evil name.pdf"))
        m = Material.objects.get()
        self.assertNotIn("evil", m.file.name)
        self.assertRegex(m.file.name, r"^materials/[0-9a-f]{32}\.pdf$")
        self.assertEqual(m.original_filename, "evil name.pdf")  # basename only
        self.assertEqual(m.content_type, "application/pdf")

    def test_cannot_put_material_in_another_users_unit(self):
        other_unit = self.make_unit(self.make_user("b@x.com"))
        r = self._post(unit=other_unit.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Material.objects.count(), 0)

    def test_replace_file_removes_old_file_from_disk(self):
        self._post(file=pdf_file())
        m = Material.objects.get()
        old = m.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("vault:material_edit", args=[m.pk]), {
                "unit": self.unit.pk, "title": "Week 1", "kind": "note", "notes": "hello",
                "file": pdf_file("new.pdf", 50)})
        m.refresh_from_db()
        self.assertNotEqual(m.file.name, old)
        self.assertFalse(m.file.storage.exists(old))
        self.assertTrue(m.file.storage.exists(m.file.name))

    def test_delete_removes_file_and_audits(self):
        m = self.make_material(self.user, with_file=True)
        name = m.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("vault:material_delete", args=[m.pk]))
        self.assertFalse(Material.objects.filter(pk=m.pk).exists())
        self.assertFalse(m.file.storage.exists(name))
        self.assertTrue(AuditEvent.objects.filter(action="material.delete", target_id=m.pk).exists())

    def test_other_user_cannot_view_edit_delete_or_download_and_denial_is_audited(self):
        m = self.make_material(self.user, with_file=True)
        intruder = self.make_user("evil@x.com")
        self.client.logout()
        self.login(intruder)
        for name in ("vault:material_detail", "vault:material_edit", "vault:material_delete",
                     "vault:material_download"):
            self.assertEqual(self.client.get(reverse(name, args=[m.pk])).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("vault:material_delete", args=[m.pk])).status_code, 403)
        self.assertTrue(Material.objects.filter(pk=m.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(actor=intruder, action="access.denied",
                                                  outcome="denied", target_id=m.pk).exists())

    def test_owner_download_headers(self):
        m = self.make_material(self.user, with_file=True)
        r = self.client.get(reverse("vault:material_download", args=[m.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        b"".join(r.streaming_content)

    def test_library_search_and_filters_only_return_own_materials(self):
        mine = self.make_material(self.user, "Boolean algebra", unit=self.unit, kind="note")
        self.make_material(self.user, "Midterm 2023", unit=self.unit, kind="past_paper")
        secret = self.make_material(self.make_user("b@x.com"), "Boolean secrets")
        r = self.client.get(reverse("vault:material_library"), {"q": "boolean"})
        self.assertContains(r, "Boolean algebra")
        self.assertNotContains(r, "Boolean secrets")
        self.assertNotContains(r, "Midterm 2023")
        r = self.client.get(reverse("vault:material_library"), {"kind": "past_paper"})
        self.assertContains(r, "Midterm 2023")
        self.assertNotContains(r, "Boolean algebra")
        mine.tags.add(self.user.tags.create(name="exam"))
        r = self.client.get(reverse("vault:material_library"), {"tag": "exam"})
        self.assertContains(r, "Boolean algebra")
        self.assertNotContains(r, secret.title)

    def test_search_survives_garbage_params(self):
        r = self.client.get(reverse("vault:material_library"), {"q": "' OR 1=1 --", "course": "abc", "kind": "zzz"})
        self.assertEqual(r.status_code, 200)

    def test_research_metadata_validation(self):
        r = self._post(doi="not-a-doi")
        self.assertEqual(Material.objects.count(), 0)
        self._post(doi="10.1000/xyz123", authors="Smith", publication_year="2021", kind="research_paper")
        self.assertEqual(Material.objects.get().doi, "10.1000/xyz123")


class FileValidationTests(BaseTestCase):
    def test_accepts_allowed_types(self):
        self.assertEqual(validate_upload(pdf_file()), "application/pdf")
        self.assertIn("wordprocessingml", validate_upload(docx_file()))
        self.assertEqual(validate_upload(SimpleUploadedFile("a.txt", b"hello")), "text/plain; charset=utf-8")
        png = SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 10)
        self.assertEqual(validate_upload(png), "image/png")

    def test_rejects_disallowed_extensions(self):
        for name in ("run.exe", "page.html", "x.php", "noext", "a.svg", "x.pdf.exe"):
            with self.assertRaises(ValidationError, msg=name):
                validate_upload(SimpleUploadedFile(name, b"data"))

    def test_rejects_content_that_does_not_match_extension(self):
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("fake.pdf", b"MZ\x90\x00 this is an exe"))
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("fake.docx", b"not a zip"))
        with self.assertRaises(ValidationError):  # valid zip but not a docx
            import io, zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("evil.txt", "x")
            validate_upload(SimpleUploadedFile("fake.docx", buf.getvalue()))
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("bin.txt", b"abc\x00def"))

    def test_rejects_empty_and_oversized(self):
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("e.pdf", b""))
        with override_settings(MAX_UPLOAD_BYTES=50):
            with self.assertRaises(ValidationError):
                validate_upload(pdf_file(size=500))

    def test_oversized_upload_rejected_through_the_form(self):
        user = self.make_user("a@x.com")
        unit = self.make_unit(user)
        self.login(user)
        with override_settings(MAX_UPLOAD_BYTES=50):
            r = self.client.post(reverse("vault:material_create"), {
                "unit": unit.pk, "title": "Big", "kind": "note", "file": pdf_file(size=500)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Material.objects.count(), 0)
