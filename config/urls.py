from django.contrib import admin
from django.urls import include, path

handler403 = "apps.core.views.permission_denied"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("vault/", include("apps.vault.urls")),
    path("timetable/", include("apps.planner.urls")),
    path("study-assistant/", include("apps.study_assistant.urls")),
    path("", include("apps.sharing.urls")),
    path("", include("apps.core.urls")),
]
