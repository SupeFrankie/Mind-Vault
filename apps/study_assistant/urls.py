"""URL routing for Study Assistant."""
from django.urls import path

from . import views

app_name = "study_assistant"

urlpatterns = [
    path("api/generate/", views.generate_study_content_view, name="generate"),
    path("api/documents/", views.study_document_list_view, name="document_list"),
    path("api/documents/<int:document_id>/", views.study_document_detail_view, name="document_detail"),
]
