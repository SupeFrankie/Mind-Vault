from django.conf import settings
from django.db import models


class StudyDocument(models.Model):
    """Uploaded or ingested study document (PDF or text)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_documents",
    )
    group = models.ForeignKey(
        "sharing.StudyGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_documents",
        help_text="Optional study group association for group study resources.",
    )
    title = models.CharField(max_length=255)
    document_file = models.FileField(
        upload_to="study_docs/%Y/%m/",
        blank=True,
        null=True,
    )
    raw_text = models.TextField(blank=True)
    extracted_text = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.user.email})"


class GeneratedNote(models.Model):
    """Personal Study Guide: Summaries, Explanations, and Structured Notes."""

    document = models.OneToOneField(
        StudyDocument,
        on_delete=models.CASCADE,
        related_name="generated_note",
    )
    summary = models.TextField(help_text="High-level overview of the ingested text.")
    explanation = models.TextField(
        blank=True,
        help_text="Detailed conceptual explanation of the key themes.",
    )
    structured_notes = models.JSONField(
        default=list,
        help_text="List of bulleted, detailed study notes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notes for {self.document.title}"


class Flashcard(models.Model):
    """Personal Study Guide: Question and Answer flashcard pairs."""

    document = models.ForeignKey(
        StudyDocument,
        on_delete=models.CASCADE,
        related_name="flashcards",
    )
    question = models.TextField()
    answer = models.TextField()
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Flashcard #{self.id}: {self.question[:40]}..."


class QuizQuestion(models.Model):
    """Personal Study Guide: Multiple-choice quizzes with answer keys."""

    document = models.ForeignKey(
        StudyDocument,
        on_delete=models.CASCADE,
        related_name="quiz_questions",
    )
    question = models.TextField()
    options = models.JSONField(
        default=list,
        help_text="List of options, e.g. ['A) ...', 'B) ...', 'C) ...', 'D) ...']",
    )
    correct_answer = models.CharField(
        max_length=255,
        help_text="Correct answer key or option text.",
    )
    explanation = models.TextField(
        blank=True,
        help_text="Explanation of why this answer is correct.",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Quiz #{self.id}: {self.question[:40]}..."


class GroupStudyResource(models.Model):
    """Group Study Resources: Revision Summaries, Material Conflicts, and Unanswered Queries."""

    document = models.OneToOneField(
        StudyDocument,
        on_delete=models.CASCADE,
        related_name="group_resource",
    )
    revision_summary = models.TextField(
        help_text="Synthesized revision overview for the entire study group.",
    )
    material_conflicts = models.JSONField(
        default=list,
        help_text="Contradictory information or discrepancies identified across materials.",
    )
    unanswered_queries = models.JSONField(
        default=list,
        help_text="Ambiguous concepts or missing information requiring further group clarification.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Group Resource for {self.document.title}"


class GroupSessionTask(models.Model):
    """Group Study Resources: Suggested actionable tasks for the next group study meeting."""

    resource = models.ForeignKey(
        GroupStudyResource,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"Task: {self.title}"
