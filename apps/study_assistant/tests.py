import json
from unittest.mock import patch, MagicMock
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.sharing.models import StudyGroup, Membership
from .models import (
    Flashcard,
    GeneratedNote,
    GroupSessionTask,
    GroupStudyResource,
    QuizQuestion,
    StudyDocument,
)
from .services import (
    LLMServiceError,
    clean_and_parse_json,
    extract_text_from_file,
    save_generated_content,
)

User = get_user_model()

MOCK_LLM_RESPONSE = {
    "personal_study_guide": {
        "summary": "This document covers database normalization and indexing.",
        "explanation": "Normalization minimizes redundancy through functional dependencies.",
        "structured_notes": [
            "1NF eliminates repeating groups.",
            "2NF eliminates partial dependency on composite keys.",
            "3NF eliminates transitive dependencies.",
        ],
        "flashcards": [
            {
                "question": "What is Third Normal Form (3NF)?",
                "answer": "A relation is in 3NF if it is in 2NF and has no transitive dependencies.",
            },
            {
                "question": "What is a primary key?",
                "answer": "A minimal superkey chosen to uniquely identify tuples in a relation.",
            },
        ],
        "quizzes": [
            {
                "question": "Which normal form requires removing transitive dependencies?",
                "options": ["A) 1NF", "B) 2NF", "C) 3NF", "D) BCNF"],
                "correct_answer": "C",
                "explanation": "3NF specifically addresses transitive dependencies on non-prime attributes.",
            }
        ],
    },
    "group_study_resources": {
        "revision_summary": "Group focus on normal forms 1NF to 3NF and ACID properties.",
        "material_conflicts": [
            {
                "topic": "BCNF vs 3NF Trade-offs",
                "conflict": "Section 3 claims BCNF preserves all functional dependencies, whereas Chapter 4 notes dependency preservation may be sacrificed.",
            }
        ],
        "unanswered_queries": [
            "Does PostgreSQL enforce multi-valued dependencies automatically in 4NF?",
        ],
        "next_session_tasks": [
            {
                "title": "Practice Decomposition Problems",
                "description": "Each member solves 2 decomposition exercises to test for lossless-join property.",
            }
        ],
    },
}


class StudyAssistantServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("student@example.com", "SecurePass123!", full_name="Student One")
        self.doc = StudyDocument.objects.create(
            user=self.user,
            title="Database Systems",
            raw_text="Database normalization is the process of structuring relational database...",
        )

    def test_clean_and_parse_json_with_fences(self):
        markdown_wrapped = f"```json\n{json.dumps(MOCK_LLM_RESPONSE)}\n```"
        parsed = clean_and_parse_json(markdown_wrapped)
        self.assertEqual(parsed["personal_study_guide"]["summary"], MOCK_LLM_RESPONSE["personal_study_guide"]["summary"])

    def test_clean_and_parse_json_raw(self):
        raw_json = json.dumps(MOCK_LLM_RESPONSE)
        parsed = clean_and_parse_json(raw_json)
        self.assertIn("personal_study_guide", parsed)
        self.assertIn("group_study_resources", parsed)

    def test_clean_and_parse_json_invalid_raises(self):
        with self.assertRaises(LLMServiceError):
            clean_and_parse_json("Not a json payload at all")

    def test_extract_text_from_plain_file(self):
        file = SimpleUploadedFile("notes.txt", b"Hello text content", content_type="text/plain")
        text = extract_text_from_file(file)
        self.assertEqual(text, "Hello text content")

    def test_save_generated_content_creates_all_models(self):
        save_generated_content(self.doc, MOCK_LLM_RESPONSE)
        self.doc.refresh_from_db()

        self.assertEqual(self.doc.status, StudyDocument.Status.COMPLETED)

        # 1. Notes
        note = self.doc.generated_note
        self.assertEqual(note.summary, MOCK_LLM_RESPONSE["personal_study_guide"]["summary"])
        self.assertEqual(len(note.structured_notes), 3)

        # 2. Flashcards
        flashcards = self.doc.flashcards.all()
        self.assertEqual(flashcards.count(), 2)
        self.assertEqual(flashcards[0].question, "What is Third Normal Form (3NF)?")

        # 3. Quizzes
        quizzes = self.doc.quiz_questions.all()
        self.assertEqual(quizzes.count(), 1)
        self.assertEqual(quizzes[0].correct_answer, "C")

        # 4. Group Resources
        group_res = self.doc.group_resource
        self.assertEqual(group_res.revision_summary, MOCK_LLM_RESPONSE["group_study_resources"]["revision_summary"])
        self.assertEqual(len(group_res.material_conflicts), 1)
        self.assertEqual(len(group_res.unanswered_queries), 1)

        # 5. Group Tasks
        tasks = group_res.tasks.all()
        self.assertEqual(tasks.count(), 1)
        self.assertEqual(tasks[0].title, "Practice Decomposition Problems")


class StudyAssistantAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice@example.com", "SecurePass123!", full_name="Alice Smith")
        self.other_user = User.objects.create_user("bob@example.com", "SecurePass123!", full_name="Bob Jones")

    def test_generate_requires_authentication(self):
        r = self.client.post(
            reverse("study_assistant:generate"),
            data={"title": "Test", "text": "Content"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    def test_generate_rejects_empty_content(self):
        self.client.force_login(self.user)
        r = self.client.post(
            reverse("study_assistant:generate"),
            data={"title": "Empty Document"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    @patch("apps.study_assistant.services.call_llm_api")
    def test_generate_success_creates_records_and_returns_payload(self, mock_call_llm):
        mock_call_llm.return_value = MOCK_LLM_RESPONSE
        self.client.force_login(self.user)

        r = self.client.post(
            reverse("study_assistant:generate"),
            data={
                "title": "Computer Networks",
                "text": "OSI model consists of 7 layers from Physical to Application.",
            },
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()

        # Validate schema in response
        self.assertIn("document", data)
        self.assertEqual(data["document"]["title"], "Computer Networks")
        self.assertEqual(data["document"]["status"], "completed")

        self.assertIn("personal_study_guide", data)
        self.assertEqual(len(data["personal_study_guide"]["flashcards"]), 2)
        self.assertEqual(len(data["personal_study_guide"]["quizzes"]), 1)

        self.assertIn("group_study_resources", data)
        self.assertEqual(len(data["group_study_resources"]["material_conflicts"]), 1)
        self.assertEqual(len(data["group_study_resources"]["next_session_tasks"]), 1)

    def test_detail_view_permissions(self):
        doc = StudyDocument.objects.create(
            user=self.user,
            title="Private Notes",
            raw_text="Secret algorithm.",
            status=StudyDocument.Status.COMPLETED,
        )
        save_generated_content(doc, MOCK_LLM_RESPONSE)

        # Owner can view
        self.client.force_login(self.user)
        r = self.client.get(reverse("study_assistant:document_detail", args=[doc.id]))
        self.assertEqual(r.status_code, 200)

        # Other user gets 403 Forbidden
        self.client.force_login(self.other_user)
        r = self.client.get(reverse("study_assistant:document_detail", args=[doc.id]))
        self.assertEqual(r.status_code, 403)

    def test_list_view_returns_user_documents(self):
        StudyDocument.objects.create(user=self.user, title="Doc 1", raw_text="Content 1")
        StudyDocument.objects.create(user=self.user, title="Doc 2", raw_text="Content 2")
        StudyDocument.objects.create(user=self.other_user, title="Bob Doc", raw_text="Bob Content")

        self.client.force_login(self.user)
        r = self.client.get(reverse("study_assistant:document_list"))
        self.assertEqual(r.status_code, 200)
        docs = r.json()["documents"]
        self.assertEqual(len(docs), 2)
        titles = [d["title"] for d in docs]
        self.assertIn("Doc 1", titles)
        self.assertIn("Doc 2", titles)
        self.assertNotIn("Bob Doc", titles)

    def test_dashboard_page_renders_for_logged_in_user(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("study_assistant:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AI Study Assistant")
        self.assertContains(r, "Personal AI Study Guide")
        self.assertContains(r, "Group Study Resources")

    def test_toggle_task_completion(self):
        doc = StudyDocument.objects.create(user=self.user, title="Task Doc", raw_text="Sample")
        gr = GroupStudyResource.objects.create(document=doc, revision_summary="Summary")
        task = GroupSessionTask.objects.create(resource=gr, title="Review Chapter 1", is_completed=False)

        self.client.force_login(self.user)
        url = reverse("study_assistant:toggle_task", kwargs={"task_id": task.id})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_completed"])
        task.refresh_from_db()
        self.assertTrue(task.is_completed)

        # Toggle back
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_completed"])
        task.refresh_from_db()
        self.assertFalse(task.is_completed)

