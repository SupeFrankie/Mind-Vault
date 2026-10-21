from django.urls import reverse

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase
from apps.sharing.models import Membership, Share, StudyGroup


class SaferSettingsTests(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("safer-owner@example.com", "Owner")
        self.member = self.make_user("safer-member@example.com", "Member")
        self.group = StudyGroup.objects.create(name="Safer settings group", owner=self.owner)
        Membership.objects.create(group=self.group, user=self.owner, role=Membership.Role.OWNER)
        Membership.objects.create(group=self.group, user=self.member)
        self.material = self.make_material(
            self.owner, "Safer settings material", sensitivity="internal"
        )
        self.login(self.owner)

    def _create_editor_share(self):
        response = self.client.post(reverse("sharing:share_manage", args=[self.material.pk]), {
            "group": self.group.pk,
            "permission": Share.Permission.EDIT,
            "allow_download": "on",
            "confirm_risk": "on",
        })
        self.assertEqual(response.status_code, 302)
        return Share.objects.get(material=self.material, group=self.group)

    def _safer_url(self, share):
        return reverse("sharing:share_safer_settings", args=[share.pk])

    def test_owner_applies_server_derived_recommendation_and_audits_before_after(self):
        share = self._create_editor_share()
        management = self.client.get(reverse("sharing:share_manage", args=[self.material.pk]))
        self.assertContains(management, "Use safer settings")

        response = self.client.post(self._safer_url(share), {
            # Crafted values are ignored; the route accepts no permission fields.
            "permission": "edit", "allow_download": "on", "risk_score": "0",
        })

        self.assertEqual(response.status_code, 302)
        share.refresh_from_db()
        self.assertEqual((share.permission, share.allow_download), (Share.Permission.VIEW, False))
        self.assertEqual((share.risk_score, share.risk_level), (20, "low"))
        event = AuditEvent.objects.get(action="share.update", detail__reason="safer_settings_recommendation")
        self.assertEqual(event.actor, self.owner)
        self.assertEqual((event.target_type, event.target_id), ("material", self.material.pk))
        self.assertEqual(event.detail["share_id"], share.pk)
        self.assertEqual(event.detail["before"], {"permission": Share.Permission.EDIT, "allow_download": True})
        self.assertEqual(event.detail["after"], {"permission": Share.Permission.VIEW, "allow_download": False})
        page = self.client.get(response["Location"])
        self.assertContains(page, "Safer settings applied")

    def test_non_owner_cannot_apply_recommendation(self):
        share = self._create_editor_share()
        self.client.logout()
        self.login(self.member)

        response = self.client.post(self._safer_url(share))

        self.assertEqual(response.status_code, 403)
        share.refresh_from_db()
        self.assertEqual((share.permission, share.allow_download), (Share.Permission.EDIT, True))
        self.assertFalse(AuditEvent.objects.filter(
            action="share.update", detail__reason="safer_settings_recommendation"
        ).exists())

    def test_revoked_or_stale_share_cannot_be_changed(self):
        share = self._create_editor_share()
        self.client.post(reverse("sharing:share_revoke", args=[share.pk]))

        response = self.client.post(self._safer_url(share))

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Share.objects.filter(pk=share.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(
            action="share.update", detail__reason="safer_settings_recommendation"
        ).exists())

    def test_policy_blocked_legacy_share_is_rejected_without_changes(self):
        share = self._create_editor_share()
        management = self.client.get(reverse("sharing:share_manage", args=[self.material.pk]))
        self.assertContains(management, "Use safer settings")
        self.material.sensitivity = "confidential"
        self.material.save(update_fields=["sensitivity"])

        response = self.client.post(self._safer_url(share))

        self.assertEqual(response.status_code, 302)
        share.refresh_from_db()
        self.assertEqual(share.permission, Share.Permission.EDIT)
        self.assertFalse(AuditEvent.objects.filter(
            action="share.update", detail__reason="safer_settings_recommendation"
        ).exists())
        page = self.client.get(response["Location"])
        self.assertContains(page, "no policy-approved safer settings")
        self.assertNotContains(page, "Use safer settings")

    def test_owner_membership_is_rechecked(self):
        share = self._create_editor_share()
        Membership.objects.filter(group=self.group, user=self.owner).delete()

        response = self.client.post(self._safer_url(share))

        self.assertEqual(response.status_code, 302)
        share.refresh_from_db()
        self.assertEqual((share.permission, share.allow_download), (Share.Permission.EDIT, True))
        self.assertFalse(AuditEvent.objects.filter(
            action="share.update", detail__reason="safer_settings_recommendation"
        ).exists())
        page = self.client.get(response["Location"])
        self.assertContains(page, "must remain a member of the group")
