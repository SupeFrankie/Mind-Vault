from django.urls import path

from . import views as v

app_name = "vault"
urlpatterns = [
    path("", v.semester_list, name="semester_list"),
    path("semesters/new/", v.semester_create, name="semester_create"),
    path("semesters/<int:pk>/", v.semester_detail, name="semester_detail"),
    path("semesters/<int:pk>/edit/", v.semester_edit, name="semester_edit"),
    path("semesters/<int:pk>/delete/", v.semester_delete, name="semester_delete"),
    path("semesters/<int:semester_id>/courses/new/", v.course_create, name="course_create"),
    path("courses/<int:pk>/", v.course_detail, name="course_detail"),
    path("courses/<int:pk>/edit/", v.course_edit, name="course_edit"),
    path("courses/<int:pk>/delete/", v.course_delete, name="course_delete"),
    path("courses/<int:course_id>/units/new/", v.unit_create, name="unit_create"),
    path("units/<int:pk>/", v.unit_detail, name="unit_detail"),
    path("units/<int:pk>/edit/", v.unit_edit, name="unit_edit"),
    path("units/<int:pk>/delete/", v.unit_delete, name="unit_delete"),
    path("materials/", v.material_library, name="material_library"),
    path("materials/new/", v.material_create, name="material_create"),
    path("materials/<int:pk>/", v.material_detail, name="material_detail"),
    path("materials/<int:pk>/edit/", v.material_edit, name="material_edit"),
    path("materials/<int:pk>/delete/", v.material_delete, name="material_delete"),
    path("materials/<int:pk>/download/", v.material_download, name="material_download"),
]
