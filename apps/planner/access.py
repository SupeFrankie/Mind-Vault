from django.core.exceptions import PermissionDenied
from django.http import Http404

from apps.core import audit


def get_owned_entry(request, pk):
    from .models import Entry

    entry = Entry.objects.select_related("course").filter(pk=pk).first()
    if entry is None:
        raise Http404("Not found.")
    if entry.owner_id != request.user.pk:
        audit.record("access.denied", "denied", request=request, target=entry,
                     area="planner", method=request.method)
        raise PermissionDenied("You do not have access to this entry.")
    return entry
