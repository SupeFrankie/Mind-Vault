from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: nobody, including superusers, edits audit history through the admin."""

    list_display = ("created_at", "action", "outcome", "actor_email", "target_type", "target_id")
    list_filter = ("outcome", "action")
    search_fields = ("actor_email", "action")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
