from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import PASSWORD

User = get_user_model()


class AccountsTests(TestCase):
    def test_register_creates_user_logs_in_and_hashes_password(self):
        r = self.client.post(reverse("accounts:register"), {
            "email": "Ann@Example.com", "full_name": "Ann", "university": "Riara",
            "password1": PASSWORD, "password2": PASSWORD})
        self.assertRedirects(r, reverse("core:dashboard"))
        u = User.objects.get(email="ann@example.com")  # lower-cased
        self.assertNotEqual(u.password, PASSWORD)
        self.assertIn("_auth_user_id", self.client.session)

    def test_register_rejects_duplicate_email_case_insensitively(self):
        User.objects.create_user("a@x.com", PASSWORD, full_name="A")
        r = self.client.post(reverse("accounts:register"), {
            "email": "A@X.com", "full_name": "B", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_register_rejects_weak_or_mismatched_password(self):
        r = self.client.post(reverse("accounts:register"), {
            "email": "w@x.com", "full_name": "W", "password1": "12345678", "password2": "12345678"})
        self.assertEqual(User.objects.count(), 0)
        r = self.client.post(reverse("accounts:register"), {
            "email": "w@x.com", "full_name": "W", "password1": PASSWORD, "password2": PASSWORD + "x"})
        self.assertEqual(User.objects.count(), 0)

    def test_login_and_invalid_credentials(self):
        User.objects.create_user("a@x.com", PASSWORD, full_name="A")
        bad = self.client.post(reverse("accounts:login"), {"username": "a@x.com", "password": "nope"})
        self.assertEqual(bad.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        ok = self.client.post(reverse("accounts:login"), {"username": "A@x.com", "password": PASSWORD})
        self.assertRedirects(ok, reverse("core:dashboard"))

    def test_logout_requires_post(self):
        User.objects.create_user("a@x.com", PASSWORD, full_name="A")
        self.client.login(username="a@x.com", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authentication_required_views_redirect_to_login(self):
        for name in ("core:dashboard", "vault:semester_list", "vault:material_library",
                     "sharing:group_list", "sharing:shared_list", "accounts:profile", "core:activity"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), r["Location"], name)

    def test_profile_update(self):
        u = User.objects.create_user("a@x.com", PASSWORD, full_name="A")
        self.client.login(username="a@x.com", password=PASSWORD)
        self.client.post(reverse("accounts:profile"), {"full_name": "New Name", "university": "UoN"})
        u.refresh_from_db()
        self.assertEqual(u.full_name, "New Name")
