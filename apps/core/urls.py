from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("activity/", views.activity, name="activity"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("cookies/", views.cookie_policy, name="cookies"),
]
