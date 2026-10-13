from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core import audit

from .access import get_owned
from .files import file_response
from .forms import CourseForm, MaterialForm, SemesterForm, UnitForm
from .models import Course, Material, Semester, Tag, Unit
from .services import filter_materials


def _form_page(request, form, title, cancel_url, submit="Save", multipart=False):
    return render(
        request,
        "vault/form.html",
        {"form": form, "title": title, "cancel_url": cancel_url, "submit": submit, "multipart": multipart},
    )


def _confirm_delete(request, obj, label, cancel_url, detail=""):
    return render(
        request,
        "vault/confirm_delete.html",
        {"object": obj, "label": label, "cancel_url": cancel_url, "detail": detail},
    )


# ---------- semesters ----------
@login_required
def semester_list(request):
    semesters = Semester.objects.filter(owner=request.user)
    return render(request, "vault/semester_list.html", {"semesters": semesters})


@login_required
def semester_create(request):
    form = SemesterForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        semester = form.save()
        messages.success(request, f"Semester '{semester.name}' created.")
        return redirect("vault:semester_detail", pk=semester.pk)
    return _form_page(request, form, "New semester", "/vault/")


@login_required
def semester_detail(request, pk):
    semester = get_owned(request, Semester, pk)
    return render(request, "vault/semester_detail.html", {"semester": semester, "courses": semester.courses.all()})


@login_required
def semester_edit(request, pk):
    semester = get_owned(request, Semester, pk)
    form = SemesterForm(request.POST or None, instance=semester, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Semester updated.")
        return redirect("vault:semester_detail", pk=pk)
    return _form_page(request, form, "Edit semester", f"/vault/semesters/{pk}/")


@login_required
def semester_delete(request, pk):
    semester = get_owned(request, Semester, pk)
    if request.method == "POST":
        semester.delete()
        messages.success(request, "Semester deleted.")
        return redirect("vault:semester_list")
    n = Material.objects.filter(unit__course__semester=semester).count()
    return _confirm_delete(request, semester, "semester", f"/vault/semesters/{pk}/",
                           f"This also deletes its courses, units and {n} material(s), and removes their shares.")


# ---------- courses ----------
@login_required
def course_create(request, semester_id):
    semester = get_owned(request, Semester, semester_id)
    form = CourseForm(request.POST or None, semester=semester)
    if request.method == "POST" and form.is_valid():
        course = form.save()
        messages.success(request, f"Course '{course.title}' added.")
        return redirect("vault:course_detail", pk=course.pk)
    return _form_page(request, form, f"New course in {semester.name}", f"/vault/semesters/{semester.pk}/")


@login_required
def course_detail(request, pk):
    course = get_owned(request, Course, pk, ("semester",))
    return render(request, "vault/course_detail.html", {"course": course, "units": course.units.all()})


@login_required
def course_edit(request, pk):
    course = get_owned(request, Course, pk, ("semester",))
    form = CourseForm(request.POST or None, instance=course, semester=course.semester)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Course updated.")
        return redirect("vault:course_detail", pk=pk)
    return _form_page(request, form, "Edit course", f"/vault/courses/{pk}/")


@login_required
def course_delete(request, pk):
    course = get_owned(request, Course, pk, ("semester",))
    semester_pk = course.semester_id
    if request.method == "POST":
        course.delete()
        messages.success(request, "Course deleted.")
        return redirect("vault:semester_detail", pk=semester_pk)
    n = Material.objects.filter(unit__course=course).count()
    return _confirm_delete(request, course, "course", f"/vault/courses/{pk}/",
                           f"This also deletes its units and {n} material(s), and removes their shares.")


# ---------- units ----------
@login_required
def unit_create(request, course_id):
    course = get_owned(request, Course, course_id, ("semester",))
    form = UnitForm(request.POST or None, course=course)
    if request.method == "POST" and form.is_valid():
        unit = form.save()
        messages.success(request, f"Unit '{unit.title}' added.")
        return redirect("vault:unit_detail", pk=unit.pk)
    return _form_page(request, form, f"New unit in {course.title}", f"/vault/courses/{course.pk}/")


@login_required
def unit_detail(request, pk):
    unit = get_owned(request, Unit, pk, ("course__semester",))
    return render(request, "vault/unit_detail.html", {"unit": unit, "materials": unit.materials.prefetch_related("tags")})


@login_required
def unit_edit(request, pk):
    unit = get_owned(request, Unit, pk, ("course__semester",))
    form = UnitForm(request.POST or None, instance=unit, course=unit.course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Unit updated.")
        return redirect("vault:unit_detail", pk=pk)
    return _form_page(request, form, "Edit unit", f"/vault/units/{pk}/")


@login_required
def unit_delete(request, pk):
    unit = get_owned(request, Unit, pk, ("course__semester",))
    course_pk = unit.course_id
    if request.method == "POST":
        unit.delete()
        messages.success(request, "Unit deleted.")
        return redirect("vault:course_detail", pk=course_pk)
    n = unit.materials.count()
    return _confirm_delete(request, unit, "unit", f"/vault/units/{pk}/",
                           f"This also deletes {n} material(s) and removes their shares.")


# ---------- materials ----------
@login_required
def material_library(request):
    """The user's own materials with search/filter. Scoped to owner BEFORE filtering."""
    qs = Material.objects.filter(owner=request.user).select_related("unit__course__semester").prefetch_related("tags")
    qs = filter_materials(qs, request.GET)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "kinds": Material.Kind.choices,
        "courses": Course.objects.filter(semester__owner=request.user),
        "tags": Tag.objects.filter(owner=request.user),
        "params": request.GET,
    }
    return render(request, "vault/material_library.html", ctx)


@login_required
def material_create(request):
    initial = {}
    unit_id = request.GET.get("unit", "")
    if unit_id.isdigit():
        initial["unit"] = int(unit_id)  # still validated against the owner's units by the form
    form = MaterialForm(request.POST or None, request.FILES or None, owner=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        material = form.save()
        messages.success(request, f"'{material.title}' saved.")
        return redirect("vault:material_detail", pk=material.pk)
    return _form_page(request, form, "Add material", "/vault/materials/", multipart=True)


@login_required
def material_detail(request, pk):
    material = get_owned(request, Material, pk, ("unit__course__semester",))
    return render(request, "vault/material_detail.html", {"material": material, "is_owner": True})


@login_required
def material_edit(request, pk):
    material = get_owned(request, Material, pk, ("unit__course__semester",))
    form = MaterialForm(request.POST or None, request.FILES or None, instance=material, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Material updated.")
        return redirect("vault:material_detail", pk=pk)
    return _form_page(request, form, "Edit material", f"/vault/materials/{pk}/", multipart=True)


@login_required
def material_delete(request, pk):
    material = get_owned(request, Material, pk)
    if request.method == "POST":
        audit.record("material.delete", "allowed", request=request, target=material, title=material.title)
        material.delete()
        messages.success(request, "Material deleted.")
        return redirect("vault:material_library")
    n = material.shares.count()
    return _confirm_delete(request, material, "material", f"/vault/materials/{pk}/",
                           f"It is currently shared with {n} group(s); those shares will be removed." if n else "")


@login_required
def material_download(request, pk):
    material = get_owned(request, Material, pk)
    return file_response(material)
