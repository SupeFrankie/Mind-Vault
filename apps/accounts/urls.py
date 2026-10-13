from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.MindVaultLoginView.as_view(), name="login"),
    path("logout/", views.MindVaultLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
]
