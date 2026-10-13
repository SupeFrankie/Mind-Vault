from django.contrib import admin

from .models import Membership, Share, StudyGroup


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    readonly_fields = ("join_code",)


admin.site.register(Membership)
admin.site.register(Share)
