from django.db.models import Q

from .models import Material


def parse_tags(raw: str) -> list[str]:
    """'Exam, exam,  Week 3' -> ['exam', 'week 3']. De-duplicated, order kept."""
    seen, out = set(), []
    for part in (raw or "").split(","):
        name = " ".join(part.lower().split())
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def filter_materials(qs, params):
    """Apply the shared search/filter contract to ANY already-permission-scoped queryset.

    Callers pass a queryset that only contains materials the user may see; this
    function only narrows it, so search can never widen access or leak titles.
    Params (all optional strings): q, kind, course, unit, tag.
    """
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(notes__icontains=q) | Q(tags__name__icontains=q) | Q(authors__icontains=q)
        )
    kind = params.get("kind")
    if kind in Material.Kind.values:
        qs = qs.filter(kind=kind)
    for key, lookup in (("course", "unit__course_id"), ("unit", "unit_id")):
        value = params.get(key)
        if value and str(value).isdigit():
            qs = qs.filter(**{lookup: int(value)})
    tag = (params.get("tag") or "").strip().lower()
    if tag:
        qs = qs.filter(tags__name=tag)
    return qs.distinct()
