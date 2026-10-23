from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("activity/", views.activity, name="activity"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("cookies/", views.cookie_policy, name="cookies"),
]
