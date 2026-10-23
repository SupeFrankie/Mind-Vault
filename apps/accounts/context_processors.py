from django.conf import settings


def clerk_context(request):
    return {
        "CLERK_PUBLISHABLE_KEY": getattr(settings, "CLERK_PUBLISHABLE_KEY", ""),
    }
