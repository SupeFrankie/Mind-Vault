import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

BASE_HTML = Path(settings.BASE_DIR, "templates", "base.html").read_text()


class PolicyPageTests(TestCase):
    def test_pages_are_public_and_use_site_layout(self):
        for name in ("core:privacy", "core:cookies"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'class="app-shell"')  # shared layout from base.html

    def test_footer_links_present_on_public_and_logged_in_pages(self):
        for page in (reverse("accounts:login"), reverse("core:privacy")):
            r = self.client.get(page)
            self.assertContains(r, reverse("core:privacy"))
            self.assertContains(r, reverse("core:cookies"))

    def test_cookie_page_documents_the_actual_cookie_names_and_lifetimes(self):
        r = self.client.get(reverse("core:cookies"))
        self.assertContains(r, f"<code>{settings.SESSION_COOKIE_NAME}</code>", html=False)
        self.assertContains(r, f"<code>{settings.CSRF_COOKIE_NAME}</code>", html=False)
        self.assertContains(r, f"{settings.SESSION_COOKIE_AGE // 86400} days")
        self.assertContains(r, f"{settings.CSRF_COOKIE_AGE // 86400} days")

    def test_every_localstorage_key_written_by_the_site_is_documented(self):
        written = set(re.findall(r'localStorage\.setItem\(\s*"([^"]+)"', BASE_HTML))
        self.assertTrue(written, "expected base.html to write at least one localStorage key")
        page = self.client.get(reverse("core:cookies")).content.decode()
        for key in written:
            self.assertIn(key, page, f"localStorage key {key!r} is not in the Cookie Policy")

    def test_no_tracking_scripts_in_base_template(self):
        for marker in ("googletagmanager", "google-analytics", "gtag(", "fbq(", "hotjar", "plausible"):
            self.assertNotIn(marker, BASE_HTML.lower(), marker)

    def test_third_party_hosts_in_base_template_are_disclosed(self):
        hosts = set(re.findall(r'https?://([a-z0-9.-]+)', BASE_HTML))
        policy = self.client.get(reverse("core:privacy")).content.decode()
        for host in hosts:
            self.assertIn(host, policy, f"{host} loaded by base.html but not disclosed in the Privacy Policy")

    def test_privacy_page_flags_unfilled_organisation_details_as_placeholders(self):
        r = self.client.get(reverse("core:privacy"))
        self.assertContains(r, 'class="placeholder"')
        self.assertContains(r, "No registered company")


class CookieDriftGuard(TestCase):
    def test_every_cookie_the_app_sets_is_documented(self):
        """Walks a realistic flow (form page, register, flash message) and checks every
        cookie name the server sets appears in the Cookie Policy."""
        from django.test import Client

        c = Client()
        seen = set()

        def collect(resp):
            seen.update(resp.cookies.keys())

        collect(c.get(reverse("accounts:login")))
        collect(c.post(reverse("accounts:register"), {
            "email": "z@x.com", "full_name": "Z",
            "password1": "a-Strong-pass-123", "password2": "a-Strong-pass-123"}))
        collect(c.post(reverse("vault:semester_create"), {"name": "S1"}))
        collect(c.get(reverse("vault:semester_list")))
        self.assertTrue({"csrftoken", "sessionid", "messages"} <= seen, seen)
        policy = c.get(reverse("core:cookies")).content.decode()
        for name in seen:
            self.assertIn(f"<code>{name}</code>", policy, f"cookie {name!r} not documented")
