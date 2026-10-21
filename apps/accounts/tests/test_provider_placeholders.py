import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.views import COMING_SOON_PROVIDERS
from apps.core.testing import PASSWORD

User = get_user_model()


class ComingSoonTests(TestCase):
    def test_each_provider_opens_its_coming_soon_page(self):
        expected = {
            "google": "Google sign-in is coming soon. For now, please use the existing sign-in method.",
            "apple": "Apple sign-in is coming soon. For now, please use the existing sign-in method.",
            "school-email": "School-email authentication is coming soon.",
        }
        self.assertEqual(set(expected), set(COMING_SOON_PROVIDERS))
        for slug, text in expected.items():
            r = self.client.get(reverse("accounts:coming_soon", args=[slug]))
            self.assertEqual(r.status_code, 200, slug)
            self.assertContains(r, text)
            self.assertContains(r, reverse("accounts:login"))  # way back

    def test_page_collects_no_credentials_and_makes_no_session(self):
        for slug in COMING_SOON_PROVIDERS:
            r = self.client.get(reverse("accounts:coming_soon", args=[slug]))
            html = r.content.decode().lower()
            self.assertNotIn("<form", html)  # anonymous visitors: nothing to submit
            self.assertNotIn('type="password"', html)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_post_is_not_accepted(self):
        r = self.client.post(reverse("accounts:coming_soon", args=["google"]), {"password": "x"})
        self.assertEqual(r.status_code, 405)

    def test_unknown_provider_is_404_not_500(self):
        for slug in ("facebook", "GOOGLE", "..", "google%00", "x" * 300, "school_email"):
            r = self.client.get(f"/accounts/auth/{slug}/")
            self.assertIn(r.status_code, (404,), slug)

    def test_login_page_lists_the_three_options_and_no_external_links(self):
        r = self.client.get(reverse("accounts:login"))
        for slug in COMING_SOON_PROVIDERS:
            self.assertContains(r, reverse("accounts:coming_soon", args=[slug]))
        for label in ("Sign in with Google", "Sign in with Apple", "Sign in with School Email"):
            self.assertContains(r, label)
        hrefs = re.findall(r'href="([^"]+)"', r.content.decode())
        for h in hrefs:
            self.assertFalse(h.startswith("http") and "fonts.g" not in h, f"external link on login page: {h}")


class ExistingAuthStillWorks(TestCase):
    def setUp(self):
        User.objects.create_user("a@x.com", PASSWORD, full_name="A")

    def test_password_login_still_works_with_provider_options_present(self):
        r = self.client.post(reverse("accounts:login"), {"username": "a@x.com", "password": PASSWORD})
        self.assertRedirects(r, reverse("core:dashboard"))

    def test_login_next_does_not_redirect_off_site(self):
        evil = self.client.post(reverse("accounts:login") + "?next=https://evil.example/",
                                {"username": "a@x.com", "password": PASSWORD, "next": "https://evil.example/"})
        self.assertRedirects(evil, reverse("core:dashboard"), fetch_redirect_response=False)

    def test_login_next_to_local_page_is_honoured(self):
        r = self.client.post(reverse("accounts:login"),
                             {"username": "a@x.com", "password": PASSWORD, "next": "/vault/"})
        self.assertRedirects(r, "/vault/", fetch_redirect_response=False)
