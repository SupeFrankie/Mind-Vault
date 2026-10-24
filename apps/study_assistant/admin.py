from django.contrib import admin

from .models import (
    Flashcard,
    GeneratedNote,
    GroupSessionTask,
    GroupStudyResource,
    QuizQuestion,
    StudyDocument,
)


class FlashcardInline(admin.TabularInline):
    model = Flashcard
    extra = 0


class QuizQuestionInline(admin.StackedInline):
    model = QuizQuestion
    extra = 0


class GeneratedNoteInline(admin.StackedInline):
    model = GeneratedNote
    extra = 0


class GroupSessionTaskInline(admin.TabularInline):
    model = GroupSessionTask
    extra = 0


@admin.register(StudyDocument)
class StudyDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "group", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("title", "user__email")
    inlines = [GeneratedNoteInline, FlashcardInline, QuizQuestionInline]


@admin.register(GroupStudyResource)
class GroupStudyResourceAdmin(admin.ModelAdmin):
    list_display = ("document", "created_at")
    inlines = [GroupSessionTaskInline]
