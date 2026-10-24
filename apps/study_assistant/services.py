"""Service layer for document text extraction, LLM API calls, and content persistence."""
import json
import logging
import os
import re
from typing import Any, Dict

from django.conf import settings
from django.db import transaction
from pypdf import PdfReader, apply_configuration
import requests

from .models import (
    Flashcard,
    GeneratedNote,
    GroupSessionTask,
    GroupStudyResource,
    QuizQuestion,
    StudyDocument,
)
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

MAX_PAGES = 30
MAX_EXTRACTED_CHARS = 100_000


class LLMServiceError(Exception):
    """Raised when the LLM service fails to generate or parse output."""
    pass


def extract_text_from_file(file_obj) -> str:
    """Extract text safely from an uploaded file (PDF or text)."""
    filename = getattr(file_obj, "name", "").lower()

    if filename.endswith(".pdf"):
        try:
            file_obj.seek(0)
            with apply_configuration(
                maximum_declared_stream_length=2_000_000,
                array_based_stream_maximum_output_length=2_000_000,
                zlib_maximum_output_length=2_000_000,
            ):
                reader = PdfReader(file_obj)
                chunks = []
                total_chars = 0
                for page_idx, page in enumerate(reader.pages[:MAX_PAGES]):
                    text = page.extract_text() or ""
                    chunks.append(text)
                    total_chars += len(text)
                    if total_chars >= MAX_EXTRACTED_CHARS:
                        break
                return "\n\n".join(chunks)[:MAX_EXTRACTED_CHARS]
        except Exception as e:
            logger.error("PDF text extraction failed: %s", e)
            raise LLMServiceError(f"Could not extract text from PDF: {e}")

    # Plain text / Markdown / other textual formats
    try:
        file_obj.seek(0)
        content = file_obj.read()
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8")[:MAX_EXTRACTED_CHARS]
            except UnicodeDecodeError:
                return content.decode("latin-1", errors="replace")[:MAX_EXTRACTED_CHARS]
        return str(content)[:MAX_EXTRACTED_CHARS]
    except Exception as e:
        logger.error("Text extraction failed: %s", e)
        raise LLMServiceError(f"Could not read document text: {e}")


def clean_and_parse_json(raw_text: str) -> Dict[str, Any]:
    """Clean markdown code fences and safely parse JSON response."""
    cleaned = raw_text.strip()

    # Remove markdown code fences if the model included them
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Find first '{' and last '}'
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        cleaned = cleaned[first_brace : last_brace + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as err:
        logger.error("JSON parsing error on response: %s", cleaned[:500])
        raise LLMServiceError(f"Failed to parse model response into JSON: {err}")


def call_llm_api(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Call either OpenRouter or Google Gemini API depending on available keys."""
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY") or getattr(settings, "OPENROUTER_API_KEY", "")
    gemini_api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", "")

    # Priority 1: OpenRouter API (supports free models like Llama 3.3 70B, Gemini Flash)
    if openrouter_api_key:
        model = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": getattr(settings, "APP_URL", "http://localhost:8000"),
            "X-Title": "MindVault Study Assistant",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            return clean_and_parse_json(raw_content)
        except Exception as e:
            logger.exception("OpenRouter API call failed")
            raise LLMServiceError(f"OpenRouter API request failed: {e}")

    # Priority 2: Google Gemini Free-tier API
    if gemini_api_key:
        model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {"parts": [{"text": user_prompt}]}
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.3,
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["candidates"][0]["content"]["parts"][0]["text"]
            return clean_and_parse_json(raw_content)
        except Exception as e:
            logger.exception("Gemini API call failed")
            raise LLMServiceError(f"Gemini API request failed: {e}")

    raise LLMServiceError(
        "No LLM API key configured. Please set OPENROUTER_API_KEY or GEMINI_API_KEY in your environment or .env."
    )


@transaction.atomic
def save_generated_content(document: StudyDocument, parsed_data: Dict[str, Any]) -> None:
    """Save the parsed JSON payload into the respective relational models."""
    personal = parsed_data.get("personal_study_guide", {})
    group = parsed_data.get("group_study_resources", {})

    # 1. Personal Study Guide: Notes
    GeneratedNote.objects.update_or_create(
        document=document,
        defaults={
            "summary": personal.get("summary", ""),
            "explanation": personal.get("explanation", ""),
            "structured_notes": personal.get("structured_notes", []),
        },
    )

    # 2. Personal Study Guide: Flashcards
    document.flashcards.all().delete()
    flashcards_data = personal.get("flashcards", [])
    flashcard_objs = [
        Flashcard(
            document=document,
            question=item.get("question", ""),
            answer=item.get("answer", ""),
            order=idx,
        )
        for idx, item in enumerate(flashcards_data)
        if item.get("question") and item.get("answer")
    ]
    if flashcard_objs:
        Flashcard.objects.bulk_create(flashcard_objs)

    # 3. Personal Study Guide: Quizzes
    document.quiz_questions.all().delete()
    quizzes_data = personal.get("quizzes", [])
    quiz_objs = [
        QuizQuestion(
            document=document,
            question=item.get("question", ""),
            options=item.get("options", []),
            correct_answer=item.get("correct_answer", ""),
            explanation=item.get("explanation", ""),
            order=idx,
        )
        for idx, item in enumerate(quizzes_data)
        if item.get("question") and item.get("options")
    ]
    if quiz_objs:
        QuizQuestion.objects.bulk_create(quiz_objs)

    # 4. Group Study Resources: Revision summary, conflicts, queries
    group_resource, _ = GroupStudyResource.objects.update_or_create(
        document=document,
        defaults={
            "revision_summary": group.get("revision_summary", ""),
            "material_conflicts": group.get("material_conflicts", []),
            "unanswered_queries": group.get("unanswered_queries", []),
        },
    )

    # 5. Group Study Resources: Next session tasks
    group_resource.tasks.all().delete()
    tasks_data = group.get("next_session_tasks", [])
    task_objs = [
        GroupSessionTask(
            resource=group_resource,
            title=item.get("title", ""),
            description=item.get("description", ""),
        )
        for item in tasks_data
        if item.get("title")
    ]
    if task_objs:
        GroupSessionTask.objects.bulk_create(task_objs)

    document.status = StudyDocument.Status.COMPLETED
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])


def process_study_document(document: StudyDocument) -> Dict[str, Any]:
    """Orchestrate document text extraction, LLM API call, and relational database persistence."""
    try:
        document.status = StudyDocument.Status.PROCESSING
        document.save(update_fields=["status", "updated_at"])

        # Extract text if document file is provided
        text_to_analyze = document.raw_text.strip()
        if document.document_file and not text_to_analyze:
            extracted = extract_text_from_file(document.document_file)
            document.extracted_text = extracted
            document.save(update_fields=["extracted_text", "updated_at"])
            text_to_analyze = extracted
        elif text_to_analyze and not document.extracted_text:
            document.extracted_text = text_to_analyze
            document.save(update_fields=["extracted_text", "updated_at"])

        if not text_to_analyze:
            raise LLMServiceError("No text content could be found or extracted from the study document.")

        user_prompt = build_user_prompt(title=document.title, text=text_to_analyze)
        parsed_data = call_llm_api(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)

        save_generated_content(document, parsed_data)
        return parsed_data

    except Exception as e:
        logger.exception("Failed processing study document ID %s", document.id)
        document.status = StudyDocument.Status.FAILED
        document.error_message = str(e)
        document.save(update_fields=["status", "error_message", "updated_at"])
        raise
