from django.test import TestCase

from apps.core import audit
from apps.core.models import AppendOnlyError, AuditEvent
from apps.core.testing import BaseTestCase


class AuditTests(BaseTestCase):
    def test_events_cannot_be_modified_or_deleted(self):
        u = self.make_user("a@x.com")
        ev = audit.record("test.event", "allowed", actor=u)
        ev.action = "changed"
        with self.assertRaises(AppendOnlyError):
            ev.save()
        with self.assertRaises(AppendOnlyError):
            ev.delete()
        with self.assertRaises(AppendOnlyError):
            AuditEvent.objects.all().update(action="x")
        with self.assertRaises(AppendOnlyError):
            AuditEvent.objects.all().delete()

    def test_history_survives_user_deletion(self):
        u = self.make_user("gone@x.com")
        audit.record("test.event", "allowed", actor=u)
        u.delete()
        ev = AuditEvent.objects.get(action="test.event")
        self.assertIsNone(ev.actor)
        self.assertEqual(ev.actor_email, "gone@x.com")

    def test_users_see_only_their_own_activity(self):
        a, b = self.make_user("a@x.com"), self.make_user("b@x.com")
        audit.record("a.event", "allowed", actor=a)
        audit.record("b.event", "allowed", actor=b)
        self.login(a)
        r = self.client.get("/activity/")
        self.assertContains(r, "a.event")
        self.assertNotContains(r, "b.event")
