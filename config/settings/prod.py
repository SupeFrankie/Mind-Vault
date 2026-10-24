"""Production settings. Run with DJANGO_SETTINGS_MODULE=config.settings.prod."""
from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, REDIS_URL

DEBUG = False
if not ALLOWED_HOSTS:
    raise RuntimeError("Set DJANGO_ALLOWED_HOSTS for production.")
if not CSRF_TRUSTED_ORIGINS:
    raise RuntimeError("Set DJANGO_CSRF_TRUSTED_ORIGINS (https://...) for production.")
if not REDIS_URL:
    raise RuntimeError("Set REDIS_URL for production group chat broadcasts.")

# HTTPS-only cookies and redirects. Do not enable until the site is served over HTTPS.
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600  # raise once you are sure HTTPS works end to end
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
