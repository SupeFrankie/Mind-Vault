from django.contrib import admin

from .models import Course, Material, Semester, Tag, Unit

for m in (Semester, Course, Unit, Tag):
    admin.site.register(m)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "kind", "unit", "updated_at")
    search_fields = ("title", "owner__email")
    readonly_fields = ("file", "original_filename", "content_type", "file_size")
