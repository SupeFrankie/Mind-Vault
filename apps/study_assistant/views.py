"""API and Views for Study Assistant generation and retrieval."""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, Http404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from apps.sharing.models import StudyGroup
from .models import StudyDocument, GroupSessionTask
from .services import LLMServiceError, process_study_document

logger = logging.getLogger(__name__)


@login_required
def study_assistant_page(request):
    """Render the AI Study Assistant interactive dashboard."""
    # Retrieve user's accessible study groups
    user_groups = StudyGroup.objects.filter(
        models.Q(owner=request.user) | models.Q(memberships__user=request.user)
    ).distinct()

    # Retrieve recent documents for the user
    recent_documents = StudyDocument.objects.filter(user=request.user).order_by("-created_at")[:10]

    return render(
        request,
        "study_assistant/dashboard.html",
        {
            "groups": user_groups,
            "recent_documents": recent_documents,
        },
    )


def serialize_document_output(document: StudyDocument) -> dict:
    """Serialize a StudyDocument and its generated study resources into a clean dict."""
    personal_guide = {}
    if hasattr(document, "generated_note"):
        note = document.generated_note
        personal_guide["summary"] = note.summary
        personal_guide["explanation"] = note.explanation
        personal_guide["structured_notes"] = note.structured_notes
    else:
        personal_guide["summary"] = ""
        personal_guide["explanation"] = ""
        personal_guide["structured_notes"] = []

    personal_guide["flashcards"] = [
        {
            "id": fc.id,
            "question": fc.question,
            "answer": fc.answer,
            "order": fc.order,
        }
        for fc in document.flashcards.all()
    ]

    personal_guide["quizzes"] = [
        {
            "id": qz.id,
            "question": qz.question,
            "options": qz.options,
            "correct_answer": qz.correct_answer,
            "explanation": qz.explanation,
            "order": qz.order,
        }
        for qz in document.quiz_questions.all()
    ]

    group_resources = {}
    if hasattr(document, "group_resource"):
        gr = document.group_resource
        group_resources["revision_summary"] = gr.revision_summary
        group_resources["material_conflicts"] = gr.material_conflicts
        group_resources["unanswered_queries"] = gr.unanswered_queries
        group_resources["next_session_tasks"] = [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "is_completed": t.is_completed,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in gr.tasks.all()
        ]
    else:
        group_resources["revision_summary"] = ""
        group_resources["material_conflicts"] = []
        group_resources["unanswered_queries"] = []
        group_resources["next_session_tasks"] = []

    return {
        "document": {
            "id": document.id,
            "title": document.title,
            "status": document.status,
            "group_id": document.group_id,
            "error_message": document.error_message,
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
        },
        "personal_study_guide": personal_guide,
        "group_study_resources": group_resources,
    }


@require_POST
def generate_study_content_view(request):
    """Endpoint to process uploaded study documents and trigger LLM generation.

    Accepts multipart/form-data or application/json.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    title = ""
    raw_text = ""
    group_id = None
    uploaded_file = None

    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"error": "Invalid JSON request body."}, status=400)
        title = body.get("title", "").strip()
        raw_text = body.get("text") or body.get("raw_text", "")
        group_id = body.get("group_id")
    else:
        title = request.POST.get("title", "").strip()
        raw_text = request.POST.get("text") or request.POST.get("raw_text", "")
        group_id = request.POST.get("group_id")
        uploaded_file = request.FILES.get("file") or request.FILES.get("document")

    if uploaded_file and not title:
        title = uploaded_file.name

    if not title:
        title = "Untitled Study Document"

    if not uploaded_file and not raw_text.strip():
        return JsonResponse(
            {"error": "Please provide a study document file (PDF/text) or paste raw_text."},
            status=400,
        )

    # Validate group association if provided
    group = None
    if group_id:
        try:
            group = StudyGroup.objects.get(pk=group_id)
            # Verify the user is a member of the group
            if not group.memberships.filter(user=request.user).exists() and group.owner != request.user:
                return JsonResponse({"error": "You are not a member of this study group."}, status=403)
        except StudyGroup.DoesNotExist:
            return JsonResponse({"error": "Study group not found."}, status=404)

    # Create the StudyDocument
    document = StudyDocument.objects.create(
        user=request.user,
        group=group,
        title=title,
        document_file=uploaded_file,
        raw_text=raw_text,
        status=StudyDocument.Status.PENDING,
    )

    try:
        process_study_document(document)
        document.refresh_from_db()
        data = serialize_document_output(document)
        return JsonResponse(data, status=201)
    except LLMServiceError as e:
        return JsonResponse(
            {
                "error": str(e),
                "document_id": document.id,
                "status": document.status,
            },
            status=502,
        )
    except Exception as e:
        logger.exception("Unexpected error processing study document: %s", e)
        return JsonResponse(
            {
                "error": f"An unexpected error occurred: {str(e)}",
                "document_id": document.id,
            },
            status=500,
        )


@require_GET
def study_document_detail_view(request, document_id):
    """Retrieve full generated study content and group resources for a document."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        document = StudyDocument.objects.select_related("generated_note", "group_resource").prefetch_related(
            "flashcards", "quiz_questions", "group_resource__tasks"
        ).get(pk=document_id)
    except StudyDocument.DoesNotExist:
        return JsonResponse({"error": "Study document not found."}, status=404)

    # Permission check: Owner or member of the associated study group
    if document.user != request.user:
        if not document.group or not document.group.memberships.filter(user=request.user).exists():
            return JsonResponse({"error": "Permission denied."}, status=403)

    return JsonResponse(serialize_document_output(document))


@require_GET
def study_document_list_view(request):
    """List all study documents accessible to the current user."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    documents = StudyDocument.objects.filter(user=request.user).order_by("-created_at")
    data = [
        {
            "id": doc.id,
            "title": doc.title,
            "status": doc.status,
            "group_id": doc.group_id,
            "created_at": doc.created_at.isoformat(),
        }
        for doc in documents
    ]
    return JsonResponse({"documents": data})


@require_POST
def toggle_task_completion_view(request, task_id):
    """Toggle completion status of a GroupSessionTask."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        task = GroupSessionTask.objects.select_related("resource__document").get(pk=task_id)
    except GroupSessionTask.DoesNotExist:
        return JsonResponse({"error": "Task not found."}, status=404)

    doc = task.resource.document
    # Permission check: owner or member of group
    if doc.user != request.user:
        if not doc.group or not doc.group.memberships.filter(user=request.user).exists():
            return JsonResponse({"error": "Permission denied."}, status=403)

    task.is_completed = not task.is_completed
    task.save(update_fields=["is_completed"])
    return JsonResponse({"success": True, "task_id": task.id, "is_completed": task.is_completed})

