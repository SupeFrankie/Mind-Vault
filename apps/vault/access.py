"""Owner-scoped lookup for vault objects.

Vault never imports `sharing` (dependency direction: sharing -> vault). Everything
in vault URLs is owner-only, so the rule is simple: the object's owner must be the
requesting user, otherwise the attempt is audited and a 403 is returned.
Non-existent ids give 404. (Trade-off: 403 vs 404 reveals that an id exists; ids
are not secret, access is what we protect. Search never reveals foreign titles.)
"""
from django.core.exceptions import PermissionDenied
from django.http import Http404

from apps.core import audit


def get_owned(request, model, pk, select_related=()):
    obj = model.objects.select_related(*select_related).filter(pk=pk).first()
    if obj is None:
        raise Http404("Not found.")
    if obj.owner_id != request.user.pk:
        audit.record(
            "access.denied", "denied", request=request, target=obj, area="vault", method=request.method
        )
        raise PermissionDenied("You do not have access to this item.")
    return obj
