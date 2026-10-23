from django.urls import reverse

from apps.core.models import Notification
from apps.core.testing import BaseTestCase
from apps.sharing.models import Membership, StudyGroup


class GroupJoinNotificationTests(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("group-owner@example.com", "Group Owner")
        self.member = self.make_user("existing-member@example.com", "Existing Member")
        self.joiner = self.make_user("new-member@example.com", "Taylor Student")
        self.outsider = self.make_user("outside-group@example.com", "Outside Student")
        self.group = StudyGroup.objects.create(name="Database Study Circle", owner=self.owner)
        Membership.objects.create(group=self.group, user=self.owner, role=Membership.Role.OWNER)
        Membership.objects.create(group=self.group, user=self.member)

    def test_successful_join_notifies_existing_members_only_and_is_idempotent(self):
        self.login(self.joiner)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                reverse("sharing:group_join"), {"join_code": self.group.join_code.lower()}
            )

        self.assertRedirects(response, reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(Notification.objects.count(), 2)
        for recipient in (self.owner, self.member):
            notification = Notification.objects.get(recipient=recipient)
            self.assertEqual(notification.message, "Taylor Student joined Database Study Circle.")
            self.assertEqual(notification.target_url, reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertFalse(Notification.objects.filter(recipient=self.joiner).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.outsider).exists())

        self.login(self.owner)
        inbox = self.client.get(reverse("core:notifications"))
        self.assertContains(inbox, "Upcoming")
        self.assertContains(inbox, "Event reminders and study-group activity.")
        self.assertEqual(inbox.content.decode().count("Taylor Student joined Database Study Circle."), 1)

        self.login(self.joiner)
        with self.captureOnCommitCallbacks(execute=True) as repeated_callbacks:
            repeated = self.client.post(
                reverse("sharing:group_join"), {"join_code": self.group.join_code}
            )
        self.assertRedirects(repeated, reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertEqual(repeated_callbacks, [])
        self.assertEqual(Notification.objects.count(), 2)

    def test_group_link_remains_protected_from_nonmembers(self):
        self.login(self.outsider)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertEqual(response.status_code, 403)
