from contextlib import nullcontext
from unittest import mock

from django.urls import reverse

from apps.core.testing import BaseTestCase, docx_file, pdf_file
from apps.vault.models import Material
from apps.vault.sensitive_data import detect_patterns, scan_pdf


class SensitiveDataScanTests(BaseTestCase):
    def test_detects_email_and_phone_categories_without_returning_values(self):
        findings = detect_patterns("Contact alex@example.org or call +1 (202) 555-0198.")
        self.assertEqual(findings, ["email", "phone"])
        self.assertNotIn("alex@example.org", repr(findings))
        self.assertNotIn("555-0198", repr(findings))

    def test_text_without_matches_is_an_empty_finding_set(self):
        self.assertEqual(detect_patterns("Lecture notes about algebra and matrices."), [])

    def test_scan_persists_categories_only_and_owner_sees_advisory(self):
        user = self.make_user("owner@example.test")
        unit = self.make_unit(user)
        self.login(user)
        page = mock.Mock()
        page.extract_text.return_value = "Contact alex@example.org or +1 202 555 0198"
        reader = mock.Mock(is_encrypted=False, pages=[page])
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", return_value=reader):
            response = self.client.post(reverse("vault:material_create"), {
                "unit": unit.pk, "title": "Contacts", "kind": "note", "sensitivity": "internal",
                "file": pdf_file(),
            })
        material = Material.objects.get(owner=user)
        self.assertRedirects(response, reverse("vault:material_detail", args=[material.pk]))
        self.assertEqual(material.sensitive_data_scan, {"status": "complete", "findings": ["email", "phone"]})
        serialized = repr(material.sensitive_data_scan)
        self.assertNotIn("alex@example.org", serialized)
        self.assertNotIn("555 0198", serialized)
        detail = self.client.get(reverse("vault:material_detail", args=[material.pk]))
        self.assertContains(detail, "Potential contact details were detected")
        self.assertContains(detail, "incomplete")
        self.assertContains(detail, "false positives")

    def test_pdf_with_no_matches_is_complete_but_does_not_claim_completeness(self):
        user = self.make_user("owner@example.test")
        unit = self.make_unit(user)
        self.login(user)
        page = mock.Mock()
        page.extract_text.return_value = "Ordinary text with no contact information."
        reader = mock.Mock(is_encrypted=False, pages=[page])
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", return_value=reader):
            self.client.post(reverse("vault:material_create"), {
                "unit": unit.pk, "title": "Plain", "kind": "note", "sensitivity": "internal",
                "file": pdf_file(),
            })
        material = Material.objects.get(owner=user)
        self.assertEqual(material.sensitive_data_scan, {"status": "complete", "findings": []})
        self.assertContains(self.client.get(reverse("vault:material_detail", args=[material.pk])),
                            "No email address or phone number patterns were detected")

    def test_scanned_image_only_pdf_is_reported_as_unavailable(self):
        page = mock.Mock()
        page.extract_text.return_value = ""
        reader = mock.Mock(is_encrypted=False, pages=[page])
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", return_value=reader):
            result = scan_pdf(pdf_file())
        self.assertEqual(result, {"status": "unavailable", "findings": []})

    def test_encrypted_pdf_is_reported_as_unavailable(self):
        reader = mock.Mock(is_encrypted=True, pages=[])
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", return_value=reader):
            result = scan_pdf(pdf_file())
        self.assertEqual(result, {"status": "unavailable", "findings": []})

    def test_malformed_or_unreadable_pdf_failure_is_reported_without_failing_upload(self):
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", side_effect=ValueError("broken PDF")):
            result = scan_pdf(pdf_file())
        self.assertEqual(result, {"status": "unavailable", "findings": []})

    def test_malformed_pdf_does_not_prevent_material_upload(self):
        user = self.make_user("owner@example.test")
        unit = self.make_unit(user)
        self.login(user)
        with mock.patch("apps.vault.sensitive_data.apply_configuration", return_value=nullcontext()), \
                mock.patch("apps.vault.sensitive_data.PdfReader", side_effect=ValueError("broken PDF")):
            response = self.client.post(reverse("vault:material_create"), {
                "unit": unit.pk, "title": "Unreadable", "kind": "note", "sensitivity": "internal",
                "file": pdf_file(),
            })
        material = Material.objects.get(owner=user)
        self.assertRedirects(response, reverse("vault:material_detail", args=[material.pk]))
        self.assertEqual(material.sensitive_data_scan, {"status": "unavailable", "findings": []})

    def test_non_pdf_upload_is_marked_not_applicable(self):
        user = self.make_user("owner@example.test")
        unit = self.make_unit(user)
        self.login(user)
        self.client.post(reverse("vault:material_create"), {
            "unit": unit.pk, "title": "Handout", "kind": "note", "sensitivity": "internal",
            "file": docx_file(),
        })
        material = Material.objects.get(owner=user)
        self.assertEqual(material.sensitive_data_scan, {"status": "not_applicable", "findings": []})

