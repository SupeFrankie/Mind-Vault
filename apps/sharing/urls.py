from django.urls import path

from . import views as v

app_name = "sharing"
urlpatterns = [
    path("groups/", v.group_list, name="group_list"),
    path("groups/new/", v.group_create, name="group_create"),
    path("groups/join/", v.group_join, name="group_join"),
    path("groups/<int:pk>/", v.group_detail, name="group_detail"),
    path("groups/<int:pk>/leave/", v.group_leave, name="group_leave"),
    path("groups/<int:pk>/delete/", v.group_delete, name="group_delete"),
    path("groups/<int:pk>/members/<int:user_id>/remove/", v.member_remove, name="member_remove"),
    path("sharing/materials/<int:material_id>/", v.share_manage, name="share_manage"),
    path("sharing/shares/<int:pk>/revoke/", v.share_revoke, name="share_revoke"),
    path("sharing/shares/<int:pk>/safer-settings/", v.share_safer_settings, name="share_safer_settings"),
    path("shared/", v.shared_list, name="shared_list"),
    path("shared/<int:material_id>/", v.shared_detail, name="shared_detail"),
    path("shared/<int:material_id>/download/", v.shared_download, name="shared_download"),
    path("shared/<int:material_id>/edit/", v.shared_edit, name="shared_edit"),
]
