from django.contrib import admin
from django.test import RequestFactory

from apps.core.models import AuditEvent
from apps.core.testing import BaseTestCase
from apps.sharing.models import Comment, Share, StudyGroup
from apps.vault.admin import MaterialAdmin
from apps.vault.models import Material


class MaterialAdminAuditTests(BaseTestCase):
    def setUp(self):
        self.actor = self.make_user("material-admin@example.com", "Admin Actor")
        self.owner = self.make_user("material-owner@example.com", "Material Owner")
        self.member = self.make_user("material-member@example.com", "Material Member")
        self.group = StudyGroup.objects.create(name="Material audit group", owner=self.owner)
        self.request = RequestFactory().post("/admin/")
        self.request.user = self.actor
        self.material_admin = MaterialAdmin(Material, admin.site)

    def test_single_material_deletion_audits_actor_target_and_share_effects(self):
        material = self.make_material(self.owner, "Single delete", sensitivity="internal")
        Share.objects.create(material=material, group=self.group, created_by=self.owner)
        Comment.objects.create(material=material, author=self.member, body="A comment")

        self.material_admin.delete_model(self.request, material)

        event = AuditEvent.objects.get(action="material.delete", target_id=material.pk)
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.target_type, "material")
        self.assertEqual(event.detail["title"], "Single delete")
        self.assertEqual(event.detail["owner_id"], self.owner.pk)
        self.assertEqual(event.detail["shares_removed"], 1)
        self.assertEqual(event.detail["comments_removed"], 1)
        self.assertFalse(Material.objects.filter(pk=material.pk).exists())

    def test_bulk_material_deletion_is_audited_once_per_material(self):
        materials = [
            self.make_material(self.owner, "Bulk one", sensitivity="internal"),
            self.make_material(self.owner, "Bulk two", sensitivity="internal"),
        ]
        for material in materials:
            Share.objects.create(material=material, group=self.group, created_by=self.owner)
        ids = [material.pk for material in materials]

        self.material_admin.delete_queryset(self.request, Material.objects.filter(pk__in=ids))

        events = list(AuditEvent.objects.filter(action="material.delete", target_id__in=ids))
        self.assertEqual(len(events), len(materials))
        self.assertEqual({event.target_id for event in events}, set(ids))
        self.assertTrue(all(event.actor_id == self.actor.pk for event in events))
        self.assertTrue(all(event.detail["shares_removed"] == 1 for event in events))
        self.assertFalse(Material.objects.filter(pk__in=ids).exists())
