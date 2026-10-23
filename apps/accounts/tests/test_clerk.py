from unittest.mock import patch, MagicMock
import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ClerkAuthTests(TestCase):
    def test_sso_callback_renders_correctly(self):
        r = self.client.get(reverse("accounts:sso_callback"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Completing sign in...")
        self.assertContains(r, "clerk.browser.js")

    def test_clerk_login_requires_post(self):
        r = self.client.get(reverse("accounts:clerk_login"))
        self.assertEqual(r.status_code, 405)

    def test_clerk_login_rejects_empty_token(self):
        r = self.client.post(
            reverse("accounts:clerk_login"),
            data={},
            content_type="application/json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertJSONEqual(r.content, {"status": "error", "message": "Missing session token."})

    @patch("apps.accounts.views.Clerk")
    def test_clerk_login_success_creates_user_and_session(self, MockClerk):
        mock_instance = MagicMock()
        MockClerk.return_value = mock_instance

        mock_email = MagicMock()
        mock_email.email_address = "googleuser@example.com"
        mock_user = MagicMock()
        mock_user.email_addresses = [mock_email]
        mock_user.first_name = "Google"
        mock_user.last_name = "User"
        mock_user.username = "googleuser"
        mock_instance.users.get.return_value = mock_user

        token = jwt.encode({"sub": "user_12345"}, "dummy_secret", algorithm="HS256")

        r = self.client.post(
            reverse("accounts:clerk_login"),
            data={"token": token},
            content_type="application/json"
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["redirect_url"], "/")

        # Verify Django session and user creation
        self.assertIn("_auth_user_id", self.client.session)
        created_user = User.objects.get(email="googleuser@example.com")
        self.assertEqual(created_user.full_name, "Google User")
