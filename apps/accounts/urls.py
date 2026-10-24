from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.MindVaultLoginView.as_view(), name="login"),
    path("logout/", views.MindVaultLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("sso-callback/", views.sso_callback, name="sso_callback"),
    path("clerk-login/", views.clerk_login, name="clerk_login"),
    path("auth/<str:provider>/", views.coming_soon, name="coming_soon"),
]
