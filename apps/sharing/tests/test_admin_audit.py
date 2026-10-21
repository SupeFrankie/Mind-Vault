from django.contrib import admin
from django.test import RequestFactory

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase
from apps.sharing.admin import MembershipAdmin, StudyGroupAdmin
from apps.sharing.models import Membership, Share, StudyGroup


class AdminAuditTests(BaseTestCase):
    def setUp(self):
        self.actor = self.make_user("admin-audit@example.com", "Admin Actor")
        self.owner = self.make_user("group-owner@example.com", "Group Owner")
        self.member = self.make_user("member@example.com", "Group Member")
        self.group = StudyGroup.objects.create(name="Audit group", owner=self.owner)
        self.request = RequestFactory().post("/admin/")
        self.request.user = self.actor

    def test_membership_creation_is_audited_with_actor_and_target_context(self):
        membership_admin = MembershipAdmin(Membership, admin.site)
        membership = Membership(group=self.group, user=self.member, role=Membership.Role.MEMBER)

        membership_admin.save_model(self.request, membership, form=None, change=False)

        event = AuditEvent.objects.get(action="group.member_add")
        self.assertEqual(event.actor, self.actor)
        self.assertEqual((event.target_type, event.target_id), ("studygroup", self.group.pk))
        self.assertEqual(event.detail["membership_id"], membership.pk)
        self.assertEqual(event.detail["member_id"], self.member.pk)
        self.assertEqual(event.detail["role"], Membership.Role.MEMBER)

    def test_membership_role_change_is_audited_but_unchanged_role_is_not(self):
        membership = Membership.objects.create(group=self.group, user=self.member)
        membership_admin = MembershipAdmin(Membership, admin.site)

        membership.role = Membership.Role.OWNER
        membership_admin.save_model(self.request, membership, form=None, change=True)
        event = AuditEvent.objects.get(action="group.member_role_change")
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.detail["before"], {"role": Membership.Role.MEMBER})
        self.assertEqual(event.detail["after"], {"role": Membership.Role.OWNER})

        membership.refresh_from_db()
        membership_admin.save_model(self.request, membership, form=None, change=True)
        self.assertEqual(AuditEvent.objects.filter(action="group.member_role_change").count(), 1)

    def test_single_and_bulk_group_deletions_are_audited_per_group_with_effects(self):
        material = self.make_material(self.owner, "Shared item", sensitivity="internal")
        Membership.objects.create(group=self.group, user=self.member)
        Share.objects.create(material=material, group=self.group, created_by=self.owner)
        group_admin = StudyGroupAdmin(StudyGroup, admin.site)

        group_admin.delete_model(self.request, self.group)
        single_event = AuditEvent.objects.get(action="group.delete", target_id=self.group.pk)
        self.assertEqual(single_event.actor, self.actor)
        self.assertEqual(single_event.detail["memberships_removed"], 1)
        self.assertEqual(single_event.detail["shares_removed"], 1)
        self.assertFalse(StudyGroup.objects.filter(pk=self.group.pk).exists())

        second = StudyGroup.objects.create(name="Second audit group", owner=self.owner)
        third = StudyGroup.objects.create(name="Third audit group", owner=self.owner)
        for group in (second, third):
            Membership.objects.create(group=group, user=self.member)
            Share.objects.create(material=material, group=group, created_by=self.owner)

        group_admin.delete_queryset(self.request, StudyGroup.objects.filter(pk__in=[second.pk, third.pk]))

        bulk_events = AuditEvent.objects.filter(action="group.delete", target_id__in=[second.pk, third.pk])
        self.assertEqual(bulk_events.count(), 2)
        self.assertTrue(all(event.actor_id == self.actor.pk for event in bulk_events))
        self.assertTrue(all(event.detail["memberships_removed"] == 1 for event in bulk_events))
        self.assertTrue(all(event.detail["shares_removed"] == 1 for event in bulk_events))
        self.assertFalse(StudyGroup.objects.filter(pk__in=[second.pk, third.pk]).exists())
