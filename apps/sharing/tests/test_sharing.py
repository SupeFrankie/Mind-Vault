from django.urls import reverse

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase
from apps.sharing import services
from apps.sharing.models import Membership, Share, StudyGroup
from apps.sharing.permissions import can_access, shared_with


class _Base(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("owner@x.com", "Olivia Owner")
        self.friend = self.make_user("friend@x.com", "Fred Friend")
        self.stranger = self.make_user("stranger@x.com", "Sam Stranger")
        self.group = self._group(self.owner, "Study Crew")
        Membership.objects.create(group=self.group, user=self.friend)
        self.material = self.make_material(self.owner, "Secret notes", with_file=True)

    def _group(self, owner, name):
        class R:  # minimal request stand-in for the service layer
            user = owner
            META = {}
        return services.create_group(R, name)

    def share(self, permission="view", download=False, confirm=False, material=None, group=None):
        self.login(self.owner)
        data = {"group": (group or self.group).pk, "permission": permission}
        if download:
            data["allow_download"] = "on"
        if confirm:
            data["confirm_risk"] = "on"
        return self.client.post(reverse("sharing:share_manage", args=[(material or self.material).pk]), data)

    def as_user(self, user):
        self.client.logout()
        self.login(user)


class GroupTests(_Base):
    def test_create_group_makes_owner_a_member_with_unique_code(self):
        self.assertTrue(Membership.objects.filter(group=self.group, user=self.owner, role="owner").exists())
        self.assertEqual(len(self.group.join_code), 8)
        other = self._group(self.owner, "Another")
        self.assertNotEqual(other.join_code, self.group.join_code)

    def test_join_with_code_and_duplicate_membership_prevented(self):
        self.as_user(self.stranger)
        code = self.group.join_code.lower()  # case-insensitive entry
        r = self.client.post(reverse("sharing:group_join"), {"join_code": code})
        self.assertRedirects(r, reverse("sharing:group_detail", args=[self.group.pk]))
        self.client.post(reverse("sharing:group_join"), {"join_code": code})
        self.assertEqual(Membership.objects.filter(group=self.group, user=self.stranger).count(), 1)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(group=self.group, user=self.stranger)

    def test_bad_join_code_rejected_and_audited(self):
        self.as_user(self.stranger)
        r = self.client.post(reverse("sharing:group_join"), {"join_code": "WRONGONE"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="group.join_failed", actor=self.stranger).exists())

    def test_only_members_can_view_group_and_only_owner_sees_code(self):
        self.as_user(self.stranger)
        self.assertEqual(self.client.get(reverse("sharing:group_detail", args=[self.group.pk])).status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(action="access.denied", actor=self.stranger).exists())
        self.as_user(self.friend)
        r = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertNotContains(r, self.group.join_code)
        self.as_user(self.owner)
        self.assertContains(self.client.get(reverse("sharing:group_detail", args=[self.group.pk])), self.group.join_code)

    def test_only_owner_can_remove_members_or_delete_group(self):
        self.as_user(self.friend)
        self.client.post(reverse("sharing:member_remove", args=[self.group.pk, self.owner.pk]))
        self.client.post(reverse("sharing:group_delete", args=[self.group.pk]))
        self.assertTrue(StudyGroup.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(Membership.objects.filter(group=self.group, user=self.owner).exists())
        self.as_user(self.owner)
        self.client.post(reverse("sharing:member_remove", args=[self.group.pk, self.friend.pk]))
        self.assertFalse(Membership.objects.filter(group=self.group, user=self.friend).exists())

    def test_owner_cannot_leave_but_member_can_and_loses_access_and_shares(self):
        self.as_user(self.owner)
        self.client.post(reverse("sharing:group_leave", args=[self.group.pk]))
        self.assertTrue(Membership.objects.filter(group=self.group, user=self.owner).exists())
        # friend shares one of their own materials, then leaves: that share disappears
        mine = self.make_material(self.friend, "Friend notes")
        Share.objects.create(material=mine, group=self.group, created_by=self.friend)
        self.share()  # owner shares Secret notes
        self.as_user(self.friend)
        self.client.post(reverse("sharing:group_leave", args=[self.group.pk]))
        self.assertFalse(Share.objects.filter(material=mine).exists())
        self.assertFalse(can_access(self.friend, self.material, "view"))

    def test_deleting_group_removes_shares_but_keeps_materials_and_audit(self):
        self.share()
        self.as_user(self.owner)
        self.client.post(reverse("sharing:group_delete", args=[self.group.pk]))
        self.assertFalse(Share.objects.exists())
        self.assertTrue(type(self.material).objects.filter(pk=self.material.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="group.delete").exists())


class PermissionTests(_Base):
    def test_group_membership_alone_grants_nothing(self):
        self.assertFalse(can_access(self.friend, self.material, "view"))
        self.as_user(self.friend)
        self.assertEqual(self.client.get(reverse("sharing:shared_detail", args=[self.material.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("sharing:shared_list")), "Secret notes")

    def test_view_share_grants_view_only(self):
        self.share("view", download=False)
        self.assertTrue(can_access(self.friend, self.material, "view"))
        self.assertFalse(can_access(self.friend, self.material, "download"))
        self.assertFalse(can_access(self.friend, self.material, "edit"))
        for action in ("replace_file", "delete", "share"):
            self.assertFalse(can_access(self.friend, self.material, action))
        self.as_user(self.friend)
        self.assertContains(self.client.get(reverse("sharing:shared_detail", args=[self.material.pk])), "Secret notes")
        self.assertContains(self.client.get(reverse("sharing:shared_list")), "Secret notes")

    def test_download_permission_enforced_independently(self):
        self.share("view", download=False)
        self.as_user(self.friend)
        self.assertEqual(self.client.get(reverse("sharing:shared_download", args=[self.material.pk])).status_code, 403)
        self.share("view", download=True)
        self.as_user(self.friend)
        r = self.client.get(reverse("sharing:shared_download", args=[self.material.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        b"".join(r.streaming_content)

    def test_edit_does_not_imply_download_and_editor_cannot_change_more_than_notes(self):
        self.share("edit", download=False, confirm=True)
        self.assertTrue(can_access(self.friend, self.material, "edit"))
        self.assertFalse(can_access(self.friend, self.material, "download"))
        self.as_user(self.friend)
        self.assertEqual(self.client.get(reverse("sharing:shared_download", args=[self.material.pk])).status_code, 403)
        self.client.post(reverse("sharing:shared_edit", args=[self.material.pk]),
                         {"title": "Edited title", "notes": "edited", "unit": 999, "owner": self.friend.pk})
        self.material.refresh_from_db()
        self.assertEqual((self.material.title, self.material.notes), ("Edited title", "edited"))
        self.assertEqual(self.material.owner_id, self.owner.pk)
        self.assertEqual(self.client.post(reverse("sharing:shared_edit", args=[self.material.pk]), {}).status_code, 200)
        # still no destructive power
        self.assertEqual(self.client.post(reverse("vault:material_delete", args=[self.material.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("sharing:share_manage", args=[self.material.pk])).status_code, 403)

    def test_view_only_member_cannot_edit(self):
        self.share("view")
        self.as_user(self.friend)
        r = self.client.post(reverse("sharing:shared_edit", args=[self.material.pk]), {"title": "Hacked", "notes": "x"})
        self.assertEqual(r.status_code, 403)
        self.material.refresh_from_db()
        self.assertEqual(self.material.title, "Secret notes")

    def test_non_member_cannot_access_shared_material_and_denials_are_audited(self):
        self.share("view", download=True)
        self.as_user(self.stranger)
        for name in ("sharing:shared_detail", "sharing:shared_download", "sharing:shared_edit"):
            self.assertEqual(self.client.get(reverse(name, args=[self.material.pk])).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("vault:material_detail", args=[self.material.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("sharing:shared_list")), "Secret notes")
        denied = AuditEvent.objects.filter(actor=self.stranger, action="access.denied", outcome="denied")
        self.assertGreaterEqual(denied.count(), 4)

    def test_share_is_per_group_not_global(self):
        other_group = self._group(self.owner, "Other")
        Membership.objects.create(group=other_group, user=self.stranger)
        self.share("view", group=self.group)
        self.assertFalse(can_access(self.stranger, self.material, "view"))
        self.assertEqual(list(shared_with(self.stranger)), [])

    def test_search_never_reveals_unshared_titles(self):
        self.make_material(self.owner, "Private midterm answers")
        self.share("view")
        self.as_user(self.friend)
        r = self.client.get(reverse("sharing:shared_list"), {"q": "midterm"})
        self.assertNotContains(r, "Private midterm answers")

    def test_revoke_removes_access_and_only_owner_can_revoke(self):
        self.share("view")
        share = Share.objects.get()
        self.as_user(self.friend)
        self.assertEqual(self.client.post(reverse("sharing:share_revoke", args=[share.pk])).status_code, 403)
        self.assertTrue(Share.objects.filter(pk=share.pk).exists())
        self.as_user(self.owner)
        self.client.post(reverse("sharing:share_revoke", args=[share.pk]))
        self.assertFalse(can_access(self.friend, self.material, "view"))
        self.assertTrue(AuditEvent.objects.filter(action="share.revoke").exists())

    def test_unknown_action_is_an_error_not_a_silent_decision(self):
        with self.assertRaises(ValueError):
            can_access(self.owner, self.material, "vieww")


class ShareCreationTests(_Base):
    def test_non_owner_cannot_share_someone_elses_material(self):
        self.as_user(self.friend)
        r = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]),
                             {"group": self.group.pk, "permission": "view"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Share.objects.exists())

    def test_cannot_share_to_group_you_do_not_belong_to(self):
        foreign = self._group(self.stranger, "Not mine")
        r = self.share(group=foreign)
        self.assertEqual(r.status_code, 200)  # form error: group not in the user's choices
        self.assertFalse(Share.objects.exists())

    def test_service_rechecks_membership_even_if_form_is_bypassed(self):
        foreign = self._group(self.stranger, "Not mine")

        class R:
            user = self.owner
            META = {}
        with self.assertRaises(services.ShareRejected):
            services.save_share(R, self.material, foreign, "view", False, False)

    def test_medium_risk_needs_confirmation_server_side(self):
        r = self.share("edit", download=True, confirm=False)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Share.objects.exists())
        self.assertContains(r, "confirm")
        r = self.share("edit", download=True, confirm=True)
        self.assertEqual(Share.objects.get().risk_level, "medium")

    def test_invalid_permission_value_in_crafted_post_rejected(self):
        self.login(self.owner)
        r = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]),
                             {"group": self.group.pk, "permission": "admin", "allow_download": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Share.objects.exists())

    def test_risk_is_recomputed_on_server_ignoring_client_claims(self):
        self.login(self.owner)
        self.client.post(reverse("sharing:share_manage", args=[self.material.pk]),
                         {"group": self.group.pk, "permission": "edit", "allow_download": "on",
                          "confirm_risk": "on", "risk_score": "0", "risk_level": "low"})
        share = Share.objects.get()
        self.assertEqual((share.risk_score, share.risk_level), (57, "medium"))

    def test_blocked_by_engine_is_audited(self):
        class R:
            user = self.owner
            META = {}
        # Group audience cannot reach 'high', so exercise the engine's block path via a stub.
        from unittest import mock
        from apps.sharing import risk
        blocked = risk.RiskAssessment(82, "high", False, False, "blocked", None)
        with mock.patch("apps.sharing.services.risk.assess_share", return_value=blocked):
            with self.assertRaises(services.ShareRejected):
                services.save_share(R, self.material, self.group, "edit", True, True)
        self.assertTrue(AuditEvent.objects.filter(action="share.blocked", outcome="blocked").exists())
        self.assertFalse(Share.objects.exists())

    def test_create_update_events_are_audited_and_upsert_keeps_one_row(self):
        self.share("view")
        self.share("edit", confirm=True)
        self.assertEqual(Share.objects.count(), 1)
        self.assertEqual(Share.objects.get().permission, "edit")
        self.assertTrue(AuditEvent.objects.filter(action="share.create", actor=self.owner).exists())
        ev = AuditEvent.objects.get(action="share.update")
        self.assertEqual(ev.detail["before"]["permission"], "view")
        self.assertEqual(ev.detail["permission"], "edit")

    def test_deleting_material_removes_its_shares(self):
        self.share("view")
        self.material.delete()
        self.assertFalse(Share.objects.exists())
