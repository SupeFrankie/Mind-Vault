from django.contrib import admin
from django.db import transaction

from apps.core import audit

from .models import Course, Material, Semester, Tag, Unit

for m in (Semester, Course, Unit, Tag):
    admin.site.register(m)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "kind", "unit", "updated_at")
    search_fields = ("title", "owner__email")
    # Sensitivity is assigned through the owner-only application workflow.
    readonly_fields = ("file", "original_filename", "content_type", "file_size", "sensitivity",
                       "sensitive_data_scan")

    def delete_model(self, request, obj):
        with transaction.atomic():
            material = Material.objects.select_for_update().filter(pk=obj.pk).first()
            if material is not None:
                audit.record(
                    "material.delete", "allowed", request=request, target=material,
                    title=material.title, owner_id=material.owner_id,
                    shares_removed=material.shares.count(),
                    comments_removed=material.comments.count(),
                )
                super().delete_model(request, material)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            material_ids = sorted(set(queryset.values_list("pk", flat=True)))
            if material_ids:
                # Lock all material roots before Django's collector deletes shares.
                materials = list(Material.objects.select_for_update().filter(
                    pk__in=material_ids
                ).order_by("pk"))
                for material in materials:
                    audit.record(
                        "material.delete", "allowed", request=request, target=material,
                        title=material.title, owner_id=material.owner_id,
                        shares_removed=material.shares.count(),
                        comments_removed=material.comments.count(),
                    )
                locked_ids = [material.pk for material in materials]
                if locked_ids:
                    super().delete_queryset(request, queryset.filter(pk__in=locked_ids))
