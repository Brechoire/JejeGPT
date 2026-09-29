from django.contrib import admin

from .models import (
    AppSettings,
    Assistant,
    Conversation,
    Export,
    LLMModel,
    Message,
    Provider,
    ScheduledRun,
    ScheduledTask,
    UsageRecord,
)


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "base_url", "is_active", "last_fetch_at")
    list_filter = ("is_active",)
    readonly_fields = ("last_fetch_at", "last_error", "created_at", "updated_at")


@admin.register(LLMModel)
class LLMModelAdmin(admin.ModelAdmin):
    list_display = ("provider", "model_id")
    list_filter = ("provider",)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("role", "content", "reasoning", "error", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "provider", "model_id", "assistant", "updated_at")
    list_filter = ("assistant",)
    inlines = [MessageInline]


@admin.register(Assistant)
class AssistantAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "model_id", "temperature", "web_search", "updated_at")
    list_filter = ("web_search", "provider")


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "default_provider", "default_model_id")


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "model_id", "total_tokens", "duration_ms", "success")
    list_filter = ("success", "route")
    readonly_fields = (
        "conversation", "provider", "model_id", "route", "prompt_tokens",
        "completion_tokens", "total_tokens", "duration_ms", "success", "error", "created_at",
    )


@admin.register(Export)
class ExportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "format", "file_name", "size")
    list_filter = ("format",)
    readonly_fields = (
        "message", "format", "download_token", "file", "file_name", "size", "created_at",
    )


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ("name", "frequency", "enabled", "next_run", "failures")
    list_filter = ("enabled", "frequency")
    readonly_fields = ("conversation", "failures", "next_run", "created_at")


@admin.register(ScheduledRun)
class ScheduledRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "task", "success", "seen")
    list_filter = ("success", "seen")
    readonly_fields = ("task", "message", "success", "error", "seen", "created_at")
