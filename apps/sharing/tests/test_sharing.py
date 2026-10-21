from django.urls import reverse
from django.db import close_old_connections, connection, connections, DatabaseError
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, skipUnlessDBFeature
from unittest import mock
import threading
import time

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase
from apps.sharing import services
from apps.sharing.models import Comment, Membership, Share, StudyGroup
from apps.sharing.permissions import can_access, shared_with
from apps.vault.models import Course, Material, Semester, Unit


class _Base(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("owner@x.com", "Olivia Owner")
        self.friend = self.make_user("friend@x.com", "Fred Friend")
        self.stranger = self.make_user("stranger@x.com", "Sam Stranger")
        self.group = self._group(self.owner, "Study Crew")
        Membership.objects.create(group=self.group, user=self.friend)
        self.material = self.make_material(self.owner, "Secret notes", with_file=True,
                                           sensitivity="internal")

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
        self.share("view", download=True, confirm=True)
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
                         {"title": "Edited title", "notes": "edited", "unit": 999, "owner": self.friend.pk,
                          "sensitivity": "highly_confidential"})
        self.material.refresh_from_db()
        self.assertEqual((self.material.title, self.material.notes), ("Edited title", "edited"))
        self.assertEqual(self.material.sensitivity, "internal")
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
        self.share("view", download=True, confirm=True)
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


class CommentTests(_Base):
    def detail_url(self):
        return reverse("sharing:shared_detail", args=[self.material.pk])

    def test_authorized_member_can_post_and_view_comment_with_server_identity_and_timestamp(self):
        self.share("view")
        self.as_user(self.friend)
        response = self.client.post(self.detail_url(), {
            "body": "Useful notes", "author": self.stranger.pk, "material": "999999",
        })
        self.assertRedirects(response, self.detail_url())
        comment = Comment.objects.get(material=self.material)
        self.assertEqual(comment.author, self.friend)
        self.assertEqual(comment.body, "Useful notes")
        self.assertIsNotNone(comment.created_at)
        self.assertTrue(AuditEvent.objects.filter(
            action="material.comment", actor=self.friend, target_id=self.material.pk
        ).exists())
        self.assertContains(self.client.get(self.detail_url()), "Useful notes")
        self.assertContains(self.client.get(self.detail_url()), "Fred Friend")

    def test_empty_and_whitespace_only_comments_are_rejected(self):
        self.share("view")
        self.as_user(self.friend)
        for body in ("", "  \t\n "):
            with self.subTest(body=body):
                response = self.client.post(self.detail_url(), {"body": body})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "This field is required.")
        self.assertFalse(Comment.objects.filter(material=self.material).exists())

    def test_comments_longer_than_the_limit_are_rejected(self):
        self.share("view")
        self.as_user(self.friend)
        response = self.client.post(self.detail_url(), {"body": "x" * 2001})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value has at most 2000 characters")
        self.assertFalse(Comment.objects.filter(material=self.material).exists())

    def test_unauthenticated_user_cannot_view_or_post_comments(self):
        self.share("view")
        self.client.logout()
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 302)
        response = self.client.post(self.detail_url(), {"body": "unauthorized"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Comment.objects.filter(material=self.material).exists())

    def test_user_without_current_access_cannot_view_or_post_comments(self):
        Comment.objects.create(material=self.material, author=self.owner, body="Private context")
        self.as_user(self.stranger)
        self.assertEqual(self.client.get(self.detail_url()).status_code, 403)
        self.assertEqual(self.client.post(self.detail_url(), {"body": "Intrusion"}).status_code, 403)
        self.assertFalse(Comment.objects.filter(material=self.material, body="Intrusion").exists())

    def test_revocation_and_membership_removal_immediately_block_comment_access(self):
        self.share("view")
        Comment.objects.create(material=self.material, author=self.owner, body="Existing comment")
        self.as_user(self.owner)
        share = Share.objects.get(material=self.material, group=self.group)
        self.client.post(reverse("sharing:share_revoke", args=[share.pk]))
        self.as_user(self.friend)
        self.assertEqual(self.client.get(self.detail_url()).status_code, 403)
        self.assertEqual(self.client.post(self.detail_url(), {"body": "After revoke"}).status_code, 403)

        self.as_user(self.owner)
        response = self.share("view")
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse("sharing:member_remove", args=[self.group.pk, self.friend.pk]))
        self.as_user(self.friend)
        self.assertEqual(self.client.get(self.detail_url()).status_code, 403)
        self.assertEqual(self.client.post(self.detail_url(), {"body": "After removal"}).status_code, 403)
        self.assertFalse(Comment.objects.filter(body__in=["After revoke", "After removal"]).exists())

    def test_material_owner_can_view_comments_without_a_group_share(self):
        comment = Comment.objects.create(material=self.material, author=self.friend, body="Owner-visible")
        self.as_user(self.owner)
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, comment.body)
        self.assertContains(response, "Fred Friend")


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
        # Confidential edit/download is deterministically blocked by sensitivity policy.
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])
        with self.assertRaises(services.ShareRejected):
            services.save_share(R, self.material, self.group, "edit", True, True)
        self.assertTrue(AuditEvent.objects.filter(action="share.blocked", outcome="blocked").exists())
        self.assertFalse(Share.objects.exists())

    def test_crafted_post_cannot_override_confidential_policy_block(self):
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])
        self.login(self.owner)
        response = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]), {
            "group": self.group.pk, "permission": "edit", "allow_download": "on",
            "confirm_risk": "on", "risk_score": "0", "risk_level": "low",
            "allowed": "true", "recommended_permission": "view",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Share.objects.exists())

    def test_service_uses_persisted_sensitivity_and_rejects_non_boolean_flags(self):
        class R:
            user = self.owner
            META = {}

        existing, _ = services.save_share(R, self.material, self.group, "view", False, False)
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])
        self.material.sensitivity = "public"  # stale/tampered in-memory instance only

        original_policy = services.policy.evaluate_share_policy

        def check_inside_transaction(sensitivity, permission, download):
            self.assertTrue(connection.in_atomic_block)
            self.assertEqual(sensitivity, "confidential")
            return original_policy(sensitivity, permission, download)

        with mock.patch("apps.sharing.services.policy.evaluate_share_policy",
                        side_effect=check_inside_transaction):
            with self.assertRaises(services.ShareRejected):
                services.save_share(R, self.material, self.group, "edit", False, True)
        for invalid in ("false", "yes", 0, 1, None):
            with self.subTest(allow_download=invalid), self.assertRaises(services.ShareRejected):
                services.save_share(R, self.material, self.group, "view", invalid, True)
        for invalid in ("on", 1, None):
            with self.subTest(confirm_risk=invalid), self.assertRaises(services.ShareRejected):
                services.save_share(R, self.material, self.group, "view", False, invalid)
        for invalid in ("admin", 1, None, ["view"]):
            with self.subTest(permission=invalid), self.assertRaises(services.ShareRejected):
                services.save_share(R, self.material, self.group, invalid, False, True)
        existing.refresh_from_db()
        self.assertEqual((existing.permission, existing.allow_download), ("view", False))
        self.assertEqual(Share.objects.count(), 1)

    def test_new_share_of_unclassified_material_is_rejected(self):
        self.material.sensitivity = None
        self.material.save(update_fields=["sensitivity"])
        self.login(self.owner)
        response = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]), {
            "group": self.group.pk, "permission": "view",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Share.objects.exists())

    def test_highly_confidential_block_has_no_safe_sharing_recommendation(self):
        self.material.sensitivity = "highly_confidential"
        self.material.save(update_fields=["sensitivity"])
        self.login(self.owner)
        response = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]), {
            "group": self.group.pk, "permission": "view", "confirm_risk": "on",
            "risk_score": "0", "recommended_permission": "view",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Highly confidential material cannot be shared")
        self.assertNotContains(response, "Recommendation:")
        self.assertFalse(Share.objects.exists())

    def test_unclassified_material_can_keep_but_not_change_existing_share(self):
        self.material.sensitivity = None
        self.material.save(update_fields=["sensitivity"])
        share = Share.objects.create(material=self.material, group=self.group, permission="view")

        class R:
            user = self.owner
            META = {}

        unchanged, created = services.save_share(R, self.material, self.group, "view", False, False)
        self.assertFalse(created)
        self.assertEqual(unchanged.pk, share.pk)
        with self.assertRaises(services.ShareRejected):
            services.save_share(R, self.material, self.group, "edit", False, True)
        share.refresh_from_db()
        self.assertEqual(share.permission, "view")

    def test_service_enforces_complete_matrix_and_confirmation(self):
        configs = [("view", False), ("view", True), ("edit", False), ("edit", True)]
        matrix = {
            "public": [(True, False), (True, False), (True, True), (True, True)],
            "internal": [(True, False), (True, True), (True, True), (True, True)],
            "confidential": [(True, True), (False, False), (False, False), (False, False)],
            "highly_confidential": [(False, False)] * 4,
        }

        class R:
            user = self.owner
            META = {}

        for sensitivity, outcomes in matrix.items():
            for (permission, download), (allowed, requires_confirmation) in zip(configs, outcomes):
                with self.subTest(sensitivity=sensitivity, permission=permission, download=download):
                    Share.objects.filter(material=self.material, group=self.group).delete()
                    self.material.sensitivity = sensitivity
                    self.material.save(update_fields=["sensitivity"])
                    if requires_confirmation:
                        with self.assertRaises(services.ShareRejected):
                            services.save_share(R, self.material, self.group, permission, download, False)
                    try:
                        services.save_share(R, self.material, self.group, permission, download, True)
                    except services.ShareRejected:
                        self.assertFalse(allowed)
                    else:
                        self.assertTrue(allowed)
                        self.assertTrue(Share.objects.filter(material=self.material, group=self.group).exists())

    def _edit_sensitivity(self, sensitivity):
        self.login(self.owner)
        return self.client.post(reverse("vault:material_edit", args=[self.material.pk]), {
            "unit": self.material.unit_id,
            "title": self.material.title,
            "kind": self.material.kind,
            "sensitivity": sensitivity,
            "notes": self.material.notes,
        })

    def test_sensitivity_increase_revokes_only_newly_blocked_shares_and_audits_state(self):
        self.share("view")  # remains permitted for Confidential, though new shares require confirmation
        allowed_share = Share.objects.get(material=self.material, group=self.group)

        blocked_edit_group = self._group(self.owner, "Edit Group")
        blocked_download_group = self._group(self.owner, "Download Group")

        class R:
            user = self.owner
            META = {}

        edit_share, _ = services.save_share(R, self.material, blocked_edit_group, "edit", False, True)
        download_share, _ = services.save_share(R, self.material, blocked_download_group, "view", True, True)
        edit_id, download_id = edit_share.pk, download_share.pk

        response = self._edit_sensitivity("confidential")
        self.assertEqual(response.status_code, 302)
        self.material.refresh_from_db()
        self.assertEqual(self.material.sensitivity, "confidential")
        allowed_share.refresh_from_db()
        self.assertEqual((allowed_share.permission, allowed_share.allow_download), ("view", False))
        self.assertEqual(Share.objects.filter(material=self.material).count(), 1)
        self.assertFalse(Share.objects.filter(pk__in=[edit_id, download_id]).exists())

        transition = AuditEvent.objects.get(action="material.sensitivity_change", target_id=self.material.pk)
        self.assertEqual(transition.actor_id, self.owner.pk)
        self.assertEqual(transition.target_type, "material")
        self.assertEqual(transition.detail["old_sensitivity"], "internal")
        self.assertEqual(transition.detail["new_sensitivity"], "confidential")
        transitions = {item["share_id"]: item for item in transition.detail["revoked_shares"]}
        self.assertEqual(transitions[edit_id]["before"], {
            "permission": "edit", "allow_download": False,
            "risk_score": edit_share.risk_score, "risk_level": edit_share.risk_level,
        })
        self.assertEqual(transitions[download_id]["before"]["allow_download"], True)
        self.assertEqual(AuditEvent.objects.filter(action="share.revoke", target_id=self.material.pk,
                                                   actor=self.owner, detail__reason="sensitivity_change").count(), 2)

    def test_sensitivity_transition_rolls_back_material_shares_and_audit_on_failure(self):
        blocked_group = self._group(self.owner, "Blocked Group")

        class R:
            user = self.owner
            META = {}

        share, _ = services.save_share(R, self.material, blocked_group, "edit", False, True)
        original_record = services.audit.record

        def fail_on_sensitivity_event(action, *args, **kwargs):
            if action == "material.sensitivity_change":
                raise RuntimeError("audit sink failure")
            return original_record(action, *args, **kwargs)

        with mock.patch("apps.core.audit.record", side_effect=fail_on_sensitivity_event):
            with self.assertRaisesRegex(RuntimeError, "audit sink failure"):
                self._edit_sensitivity("confidential")

        self.material.refresh_from_db()
        self.assertEqual(self.material.sensitivity, "internal")
        self.assertTrue(Share.objects.filter(pk=share.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(action="material.sensitivity_change",
                                                   target_id=self.material.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(action="share.revoke", target_id=self.material.pk,
                                                   detail__reason="sensitivity_change").exists())

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


class EffectiveAccessReviewTests(_Base):
    def setUp(self):
        super().setUp()
        self.other_group = self._group(self.owner, "Research Team")
        Membership.objects.create(group=self.other_group, user=self.friend)

    def _page(self):
        return self.client.get(reverse("sharing:share_manage", args=[self.material.pk]))

    def _entry(self, response, user):
        return next(entry for entry in response.context["effective_access"] if entry["user"].pk == user.pk)

    def _shared_ids_and_dashboard_count(self):
        self.as_user(self.friend)
        listing = self.client.get(reverse("sharing:shared_list"))
        dashboard = self.client.get(reverse("core:dashboard"))
        return [material.pk for material in listing.context["page"]], dashboard.context["shared_with_me_count"]

    def test_policy_eligible_share_appears_and_list_matches_dashboard_count_and_can_access(self):
        self.share("view")
        material_ids, count = self._shared_ids_and_dashboard_count()

        self.assertEqual(material_ids, [self.material.pk])
        self.assertEqual(count, 1)
        self.assertEqual(count, sum(
            can_access(self.friend, material, "view")
            for material in Material.objects.filter(owner=self.owner)
        ))

    def test_unclassified_legacy_share_is_hidden_but_record_and_owner_access_remain(self):
        self.material.sensitivity = None
        self.material.save(update_fields=["sensitivity"])
        legacy_share = Share.objects.create(
            material=self.material, group=self.group, permission="view", allow_download=False
        )

        class Request:
            user = self.owner
            META = {}

        unchanged, created = services.save_share(
            Request(), self.material, self.group, "view", False, False
        )
        self.assertFalse(created)
        self.assertEqual(unchanged.pk, legacy_share.pk)
        self.assertTrue(Share.objects.filter(pk=legacy_share.pk).exists())
        self.assertFalse(can_access(self.friend, self.material, "view"))
        material_ids, count = self._shared_ids_and_dashboard_count()
        self.assertNotIn(self.material.pk, material_ids)
        self.assertEqual(count, 0)

        self.as_user(self.owner)
        self.assertEqual(self.client.get(reverse("vault:material_detail", args=[self.material.pk])).status_code, 200)
        self.assertTrue(can_access(self.owner, self.material, "view"))
        self.assertTrue(can_access(self.owner, self.material, "download"))

    def test_policy_blocked_legacy_share_is_hidden_and_not_counted(self):
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])
        blocked = Share.objects.create(
            material=self.material, group=self.group, permission="edit", allow_download=False
        )
        unjoined_group = self._group(self.owner, "Not a recipient group")
        unrelated_valid_share = Share.objects.create(
            material=self.material, group=unjoined_group, permission="view", allow_download=False
        )
        self.assertFalse(can_access(self.friend, self.material, "view"))
        material_ids, count = self._shared_ids_and_dashboard_count()
        self.assertNotIn(self.material.pk, material_ids)
        self.assertEqual(count, 0)
        self.assertTrue(Share.objects.filter(pk=blocked.pk).exists())
        self.assertTrue(Share.objects.filter(pk=unrelated_valid_share.pk).exists())

    def test_revoked_share_and_removed_membership_stop_contributing(self):
        self.share("view")
        self.share("view", group=self.other_group)
        first_share = Share.objects.get(material=self.material, group=self.group)
        self.login(self.owner)
        self.client.post(reverse("sharing:share_revoke", args=[first_share.pk]))
        self.assertTrue(can_access(self.friend, self.material, "view"))

        self.client.post(reverse("sharing:member_remove", args=[self.other_group.pk, self.friend.pk]))
        self.assertTrue(Share.objects.filter(material=self.material, group=self.other_group).exists())
        self.assertFalse(can_access(self.friend, self.material, "view"))
        material_ids, count = self._shared_ids_and_dashboard_count()
        self.assertNotIn(self.material.pk, material_ids)
        self.assertEqual(count, 0)

    def test_owner_sees_consolidated_viewer_access_and_owner_entry(self):
        self.share("view")
        response = self._page()

        self.assertEqual(response.status_code, 200)
        friend = self._entry(response, self.friend)
        self.assertEqual(friend["access_level"], "Viewer")
        self.assertFalse(friend["download_allowed"])
        self.assertEqual(friend["view_groups"], [self.group.name])
        self.assertFalse(friend["edit_groups"])
        self.assertEqual(len([item for item in response.context["effective_access"]
                              if item["user"].pk == self.friend.pk]), 1)
        owner = self._entry(response, self.owner)
        self.assertTrue(owner["is_owner"])
        self.assertEqual(owner["access_level"], "Owner")
        self.assertTrue(can_access(self.owner, self.material, "view"))
        self.assertTrue(can_access(self.owner, self.material, "download"))

    def test_editor_and_download_capabilities_combine_across_groups(self):
        self.share("edit", download=False, confirm=True)
        self.share("view", download=True, confirm=True, group=self.other_group)
        response = self._page()
        friend = self._entry(response, self.friend)

        self.assertEqual(friend["access_level"], "Editor")
        self.assertTrue(friend["download_allowed"])
        self.assertEqual(friend["view_groups"], ["Research Team", "Study Crew"])
        self.assertEqual(friend["edit_groups"], ["Study Crew"])
        self.assertEqual(friend["download_groups"], ["Research Team"])
        self.assertTrue(friend["multiple_groups"])
        self.assertTrue(can_access(self.friend, self.material, "view"))
        self.assertTrue(can_access(self.friend, self.material, "edit"))
        self.assertTrue(can_access(self.friend, self.material, "download"))

    def test_download_endpoint_combines_eligible_grants_across_groups(self):
        self.share("view", download=True, confirm=True)
        self.share("edit", download=False, confirm=True, group=self.other_group)
        self.as_user(self.friend)
        url = reverse("sharing:shared_download", args=[self.material.pk])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        b"".join(response.streaming_content)

        self.as_user(self.owner)
        download_share = Share.objects.get(material=self.material, group=self.group)
        self.client.post(reverse("sharing:share_revoke", args=[download_share.pk]))
        self.as_user(self.friend)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_revoking_one_share_preserves_only_remaining_group_capabilities(self):
        self.share("edit", download=False, confirm=True)
        self.share("view", download=True, confirm=True, group=self.other_group)
        edit_share = Share.objects.get(material=self.material, group=self.group)
        self.login(self.owner)
        response = self.client.post(reverse("sharing:share_revoke", args=[edit_share.pk]))
        self.assertEqual(response.status_code, 302)

        access_response = self._page()
        friend = self._entry(access_response, self.friend)
        self.assertEqual(friend["access_level"], "Viewer")
        self.assertTrue(friend["download_allowed"])
        self.assertEqual(friend["view_groups"], ["Research Team"])
        self.assertEqual(friend["edit_groups"], [])
        self.assertEqual(friend["download_groups"], ["Research Team"])
        self.assertTrue(can_access(self.friend, self.material, "view"))
        self.assertFalse(can_access(self.friend, self.material, "edit"))
        self.assertTrue(can_access(self.friend, self.material, "download"))

    def test_membership_removal_removes_that_group_contribution(self):
        self.share("edit", download=False, confirm=True)
        self.share("view", download=True, confirm=True, group=self.other_group)
        self.login(self.owner)
        response = self.client.post(reverse("sharing:member_remove", args=[self.group.pk, self.friend.pk]))
        self.assertEqual(response.status_code, 302)

        access_response = self._page()
        friend = self._entry(access_response, self.friend)
        self.assertEqual(friend["access_level"], "Viewer")
        self.assertTrue(friend["download_allowed"])
        self.assertEqual(friend["view_groups"], ["Research Team"])
        self.assertEqual(friend["edit_groups"], [])
        self.assertTrue(can_access(self.friend, self.material, "view"))
        self.assertFalse(can_access(self.friend, self.material, "edit"))
        self.assertTrue(can_access(self.friend, self.material, "download"))

    def test_person_is_absent_after_all_grants_are_revoked_or_memberships_removed(self):
        self.share("view")
        self.share("view", group=self.other_group)
        self.login(self.owner)
        for share in Share.objects.filter(material=self.material):
            self.client.post(reverse("sharing:share_revoke", args=[share.pk]))
        response = self._page()
        self.assertNotIn(self.friend.pk, [entry["user"].pk for entry in response.context["effective_access"]])
        self.assertFalse(can_access(self.friend, self.material, "view"))

    def test_non_owner_cannot_retrieve_access_list_from_management_page(self):
        self.share("view")
        self.as_user(self.friend)
        response = self._page()
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "People with effective access", status_code=403)

    def test_policy_blocked_invalid_and_unclassified_grants_are_not_effective(self):
        self.login(self.owner)
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])
        blocked = Share.objects.create(
            material=self.material, group=self.group, permission="edit", allow_download=False
        )
        self.assertFalse(can_access(self.friend, self.material, "view"))
        self.assertNotIn(self.friend.pk, [entry["user"].pk for entry in
                                         self._page().context["effective_access"]])

        self.material.sensitivity = "internal"
        self.material.save(update_fields=["sensitivity"])
        blocked.permission = "invalid"
        blocked.save(update_fields=["permission"])
        self.assertFalse(can_access(self.friend, self.material, "view"))
        self.assertNotIn(self.friend.pk, [entry["user"].pk for entry in
                                         self._page().context["effective_access"]])

        blocked.permission = "view"
        blocked.save(update_fields=["permission"])
        self.material.sensitivity = None
        self.material.save(update_fields=["sensitivity"])
        self.assertFalse(can_access(self.friend, self.material, "view"))
        self.assertNotIn(self.friend.pk, [entry["user"].pk for entry in
                                         self._page().context["effective_access"]])


@skipUnlessDBFeature("has_select_for_update")
class MembershipShareConcurrencyTests(TransactionTestCase):
    """Exercise group-lock serialization with real independent DB connections."""

    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("owner-race@example.com", password="test")
        self.group_owner = User.objects.create_user("group-race@example.com", password="test")
        self.recipient = User.objects.create_user("recipient-race@example.com", password="test")
        semester = Semester.objects.create(owner=self.owner, name="Race Semester")
        course = Course.objects.create(semester=semester, title="Race Course", code="RACE")
        unit = Unit.objects.create(course=course, title="Race Unit")
        self.material = Material.objects.create(
            owner=self.owner, unit=unit, title="Race material", sensitivity="internal"
        )
        self.group = StudyGroup.objects.create(name="Race group", owner=self.group_owner)
        Membership.objects.create(group=self.group, user=self.group_owner, role=Membership.Role.OWNER)
        Membership.objects.create(group=self.group, user=self.owner)
        Membership.objects.create(group=self.group, user=self.recipient)

    def test_share_started_during_member_removal_cannot_survive_removal(self):
        removal_cleanup_reached = threading.Event()
        allow_removal_to_commit = threading.Event()
        share_connection_ready = threading.Event()
        results = {}
        original_record = services.audit.record

        class Request:
            def __init__(self, user):
                self.user = user
                self.META = {}

        def pause_after_cleanup(action, *args, **kwargs):
            if action == "group.remove_member":
                removal_cleanup_reached.set()
                if not allow_removal_to_commit.wait(30):
                    raise TimeoutError("Timed out waiting to release membership removal.")
            return original_record(action, *args, **kwargs)

        def remove_worker():
            close_old_connections()
            try:
                User = get_user_model()
                group = StudyGroup.objects.get(pk=self.group.pk)
                services.remove_member(
                    Request(User.objects.get(pk=self.group_owner.pk)), group, self.owner.pk
                )
                results["remove"] = "complete"
            except BaseException as exc:
                results["remove_error"] = exc
            finally:
                connections.close_all()

        def share_worker():
            close_old_connections()
            try:
                User = get_user_model()
                material = Material.objects.get(pk=self.material.pk)
                group = StudyGroup.objects.get(pk=self.group.pk)
                # Capture the actual MySQL connection so the coordinator can
                # observe this session waiting for the group-row lock.
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT CONNECTION_ID()")
                    results["share_connection_id"] = cursor.fetchone()[0]
                share_connection_ready.set()
                services.save_share(
                    Request(User.objects.get(pk=self.owner.pk)), material, group,
                    "view", False, False,
                )
                results["share"] = "created"
            except services.ShareRejected as exc:
                results["share"] = "rejected"
            except BaseException as exc:
                results["share_error"] = exc
            finally:
                connections.close_all()

        def wait_until_share_is_waiting_for_group_lock(connection_id):
            # The result row, rather than elapsed time, is the synchronization
            # condition. The deadline only prevents a broken test from hanging.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) "
                        "FROM performance_schema.data_lock_waits AS w "
                        "JOIN performance_schema.threads AS t "
                        "ON t.THREAD_ID = w.REQUESTING_THREAD_ID "
                        "JOIN performance_schema.data_locks AS requested "
                        "ON requested.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
                        "AND requested.OBJECT_NAME = 'sharing_studygroup' "
                        "WHERE t.PROCESSLIST_ID = %s",
                        [connection_id],
                    )
                    if cursor.fetchone()[0]:
                        return True
                # Yield briefly between database observations; this is not used
                # to infer a lock wait, only to avoid a busy loop.
                threading.Event().wait(0.01)
            return False

        if connection.vendor != "mysql":
            self.skipTest("Database lock-wait observation requires MySQL/InnoDB.")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) "
                    "FROM performance_schema.data_lock_waits AS w "
                    "JOIN performance_schema.threads AS t "
                    "ON t.THREAD_ID = w.REQUESTING_THREAD_ID "
                    "JOIN performance_schema.data_locks AS requested "
                    "ON requested.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
                    "WHERE t.PROCESSLIST_ID = -1"
                )
                cursor.fetchone()
        except DatabaseError as exc:
            self.skipTest(f"MySQL Performance Schema lock-wait visibility unavailable: {exc}")

        with mock.patch("apps.core.audit.record", side_effect=pause_after_cleanup):
            remover = threading.Thread(target=remove_worker, daemon=True)
            sharer = threading.Thread(target=share_worker, daemon=True)
            try:
                remover.start()
                self.assertTrue(removal_cleanup_reached.wait(15),
                                "removal did not reach its locked cleanup")
                sharer.start()
                self.assertTrue(share_connection_ready.wait(15),
                                "share worker did not establish its database connection")
                self.assertTrue(
                    wait_until_share_is_waiting_for_group_lock(results["share_connection_id"]),
                    "share connection never appeared in a MySQL row-lock wait",
                )
            finally:
                allow_removal_to_commit.set()
                if remover.ident is not None:
                    remover.join(15)
                if sharer.ident is not None:
                    sharer.join(15)

        self.assertFalse(remover.is_alive(), "membership removal did not finish")
        self.assertFalse(sharer.is_alive(), "share request did not finish")
        self.assertNotIn("remove_error", results, results.get("remove_error"))
        self.assertNotIn("share_error", results, results.get("share_error"))
        self.assertEqual(results.get("remove"), "complete")
        self.assertEqual(results.get("share"), "rejected")
        self.assertFalse(Membership.objects.filter(group=self.group, user=self.owner).exists())
        self.assertFalse(Share.objects.filter(material=self.material, group=self.group).exists())
        self.assertTrue(AuditEvent.objects.filter(
            action="group.remove_member", actor=self.group_owner,
            target_type="studygroup", target_id=self.group.pk,
        ).exists())
