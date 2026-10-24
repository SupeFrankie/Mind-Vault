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
from apps.vault.models import Material
from .models import StudyDocument, GroupSessionTask, StudyChatMessage
from .services import LLMServiceError, process_study_document, call_llm_chat, extract_text_from_file

logger = logging.getLogger(__name__)


@login_required
def study_assistant_page(request):
    """Render the AI Study Assistant interactive dashboard."""
    # Retrieve user's accessible study groups
    user_groups = StudyGroup.objects.filter(
        models.Q(owner=request.user) | models.Q(memberships__user=request.user)
    ).distinct()

    # Retrieve user's stored vault materials
    user_materials = Material.objects.filter(owner=request.user).select_related("unit__course__semester").order_by("-updated_at")
    selected_material_id = request.GET.get("material_id")

    # Retrieve recent documents for the user
    recent_documents = StudyDocument.objects.filter(user=request.user).select_related("material").order_by("-created_at")[:10]

    return render(
        request,
        "study_assistant/dashboard.html",
        {
            "groups": user_groups,
            "materials": user_materials,
            "selected_material_id": selected_material_id,
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

    material_data = None
    if document.material:
        material_data = {
            "id": document.material.id,
            "title": document.material.title,
            "kind": document.material.get_kind_display(),
            "has_file": document.material.has_file,
            "course": str(document.material.unit.course),
            "unit": str(document.material.unit),
        }

    chat_messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in document.chat_messages.all()
    ]

    return {
        "document": {
            "id": document.id,
            "title": document.title,
            "status": document.status,
            "group_id": document.group_id,
            "material_id": document.material_id,
            "material": material_data,
            "error_message": document.error_message,
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
        },
        "chat_messages": chat_messages,
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
    material_id = None
    uploaded_file = None

    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"error": "Invalid JSON request body."}, status=400)
        title = body.get("title", "").strip()
        raw_text = body.get("text") or body.get("raw_text", "")
        group_id = body.get("group_id")
        material_id = body.get("material_id")
    else:
        title = request.POST.get("title", "").strip()
        raw_text = request.POST.get("text") or request.POST.get("raw_text", "")
        group_id = request.POST.get("group_id")
        material_id = request.POST.get("material_id")
        uploaded_file = request.FILES.get("file") or request.FILES.get("document")

    material = None
    if material_id:
        try:
            material = Material.objects.get(pk=material_id, owner=request.user)
            if not title:
                title = material.title
        except (Material.DoesNotExist, ValueError):
            return JsonResponse({"error": "Selected material not found in your vault."}, status=404)

    if uploaded_file and not title:
        title = uploaded_file.name

    if not title:
        title = "Untitled Study Document"

    if not uploaded_file and not raw_text.strip() and not material:
        return JsonResponse(
            {"error": "Please select a material from your vault, upload a document file (PDF/text), or paste raw text."},
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
        material=material,
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


@require_POST
def chat_with_study_document_view(request):
    """Interactive chat with LLM grounded on study material or document."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    user_message = body.get("message", "").strip()
    if not user_message:
        return JsonResponse({"error": "Message cannot be empty."}, status=400)

    document_id = body.get("document_id")
    material_id = body.get("material_id")

    document = None
    if document_id:
        try:
            document = StudyDocument.objects.select_related("material").get(pk=document_id)
            if document.user != request.user:
                if not document.group or not document.group.memberships.filter(user=request.user).exists():
                    return JsonResponse({"error": "Permission denied."}, status=403)
        except StudyDocument.DoesNotExist:
            return JsonResponse({"error": "Study document not found."}, status=404)
    elif material_id:
        try:
            material = Material.objects.get(pk=material_id, owner=request.user)
            document = StudyDocument.objects.filter(user=request.user, material=material).first()
            if not document:
                document = StudyDocument.objects.create(
                    user=request.user,
                    material=material,
                    title=material.title,
                    status=StudyDocument.Status.COMPLETED,
                )
        except (Material.DoesNotExist, ValueError):
            return JsonResponse({"error": "Material not found in your vault."}, status=404)
    else:
        return JsonResponse({"error": "Either document_id or material_id is required."}, status=400)

    # Ensure document has extracted text
    if not document.extracted_text and not document.raw_text:
        material_chunks = []
        if document.material:
            if document.material.notes:
                material_chunks.append(f"Notes:\n{document.material.notes}")
            if document.material.has_file:
                try:
                    file_text = extract_text_from_file(document.material.file)
                    if file_text:
                        material_chunks.append(file_text)
                except Exception as ex:
                    logger.warning("Could not extract file text: %s", ex)
        if document.document_file:
            try:
                file_text = extract_text_from_file(document.document_file)
                if file_text:
                    material_chunks.append(file_text)
            except Exception as ex:
                logger.warning("Could not extract document_file text: %s", ex)
        if material_chunks:
            document.extracted_text = "\n\n".join(material_chunks)
            document.save(update_fields=["extracted_text", "updated_at"])

    grounding_text = (document.extracted_text or document.raw_text or "").strip()
    system_prompt = (
        f"You are an expert, encouraging AI study assistant for the student in MindVault. You are helping them study and understand their coursework: '{document.title}'.\n\n"
        f"--- Source Material ---\n{grounding_text[:15000] if grounding_text else 'No text extracted from document.'}\n--- End Source Material ---\n\n"
        "Instructions:\n"
        "1. Answer questions clearly, accurately, and concisely, grounded directly on the provided study material.\n"
        "2. Break down complex terminology into easy-to-understand explanations with intuitive analogies.\n"
        "3. When the student asks for examples, summaries, or test questions, provide structured, high-yield educational responses.\n"
        "4. If something is not covered in their material, state that clearly and offer general academic guidance."
    )

    # Get recent conversation history
    past_messages = list(document.chat_messages.order_by("-created_at")[:10])
    past_messages.reverse()
    chat_history = [
        {"role": m.role, "content": m.content}
        for m in past_messages
    ]
    chat_history.append({"role": "user", "content": user_message})

    # Save user message
    StudyChatMessage.objects.create(
        document=document,
        role=StudyChatMessage.Role.USER,
        content=user_message,
    )

    try:
        reply_text = call_llm_chat(system_prompt=system_prompt, messages=chat_history)
        asst_msg_obj = StudyChatMessage.objects.create(
            document=document,
            role=StudyChatMessage.Role.ASSISTANT,
            content=reply_text,
        )
        return JsonResponse({
            "success": True,
            "document_id": document.id,
            "reply": reply_text,
            "message_id": asst_msg_obj.id,
            "created_at": asst_msg_obj.created_at.isoformat(),
        })
    except LLMServiceError as e:
        return JsonResponse({"error": str(e), "document_id": document.id}, status=502)
    except Exception as e:
        logger.exception("Chat failure: %s", e)
        return JsonResponse({"error": f"Failed to get AI response: {str(e)}", "document_id": document.id}, status=500)


@require_GET
def get_chat_history_view(request, document_id):
    """Retrieve chat history for a study document."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        document = StudyDocument.objects.get(pk=document_id)
        if document.user != request.user:
            if not document.group or not document.group.memberships.filter(user=request.user).exists():
                return JsonResponse({"error": "Permission denied."}, status=403)
    except StudyDocument.DoesNotExist:
        return JsonResponse({"error": "Document not found."}, status=404)

    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in document.chat_messages.all()
    ]
    return JsonResponse({"messages": messages})


