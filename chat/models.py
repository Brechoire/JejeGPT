from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from django.db import models
from django.urls import reverse

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


class Provider(models.Model):
    """Endpoint OpenAI-compatible (LM Studio, Ollama, OpenRouter, vLLM...)."""

    name = models.CharField(max_length=100, unique=True)
    base_url = models.URLField(help_text="Ex. http://localhost:1234/v1 (sans /models ni /chat/completions)")
    api_key = models.CharField(max_length=500, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_fetch_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cached_models: RelatedManager[LLMModel]
    conversations: RelatedManager[Conversation]

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class LLMModel(models.Model):
    """Cache des model_id découverts via GET {base_url}/models."""

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="cached_models")
    model_id = models.CharField(max_length=300)
    display_name = models.CharField(max_length=300, blank=True, default="")
    # Route d'API connue : '' (chat ou inconnue), 'responses' ou 'messages' (non-chat).
    route = models.CharField(max_length=20, blank=True, default="")
    # Capacités : table statique à la création, réglables à la main ensuite.
    supports_effort = models.BooleanField(default=False)
    supports_web = models.BooleanField(default=False)
    supports_thinking = models.BooleanField(default=False)
    # Route d'API connue : '' (chat ou inconnue), 'responses' ou 'messages' (non-chat).
    route = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["model_id"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "model_id"], name="uniq_provider_model")
        ]

    def __str__(self) -> str:
        return f"{self.provider.name} / {self.model_id}"


class Project(models.Model):
    """Regroupe des conversations + instructions injectées en system."""

    name = models.CharField(max_length=160, unique=True)
    instructions = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    conversations: RelatedManager[Conversation]

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Conversation(models.Model):
    title = models.CharField(max_length=200, default="Nouvelle conversation")
    provider = models.ForeignKey(
        Provider, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations"
    )
    model_id = models.CharField(max_length=300, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    temperature = models.FloatField(default=0.7)
    # Options modèle (appliquées à l'envoi seulement si supportées).
    reasoning_effort = models.CharField(max_length=10, blank=True, default="")
    web_search = models.BooleanField(default=False)
    thinking_enabled = models.BooleanField(default=True)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    messages: RelatedManager[Message]

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("chat:detail", args=[self.pk])


class Message(models.Model):
    ROLE_CHOICES = [("user", "user"), ("assistant", "assistant"), ("system", "system")]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    feedback = models.CharField(
        max_length=4, blank=True, default="",
        choices=[("up", "Bonne réponse"), ("down", "Mauvaise réponse")],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:60]}"


class AppSettings(models.Model):
    """Singleton des réglages globaux (LLM par défaut des nouvelles conversations)."""

    TEXT_SCALES = (87, 100, 112, 125)

    default_provider = models.ForeignKey(
        Provider, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    default_model_id = models.CharField(max_length=300, blank=True, default="")
    text_scale = models.PositiveIntegerField(default=100)
    memory_enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return "Réglages"

    @classmethod
    def get_solo(cls) -> AppSettings:
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def default_valid(self) -> bool:
        """Défaut utilisable : provider actif + modèle en cache."""
        p = self.default_provider
        return bool(
            p is not None
            and p.is_active
            and self.default_model_id
            and p.cached_models.filter(model_id=self.default_model_id).exists()
        )

    def scale_factor(self) -> int:
        """Palier de taille de texte en %, repli 100 si hors liste."""
        try:
            value = int(self.text_scale)
        except (TypeError, ValueError):
            return 100
        return value if value in self.TEXT_SCALES else 100


class MemoryFact(models.Model):
    """Fait durable sur l'utilisateur, extrait auto après les réponses."""

    content = models.CharField(max_length=300)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.content[:60]


class DeepResearchRun(models.Model):
    """Recherche approfondie bornée vers une conversation dédiée."""

    STATUS_CHOICES = [
        ("running", "En cours"),
        ("done", "Terminée"),
        ("error", "En erreur"),
    ]

    subject = models.CharField(max_length=300)
    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="research_runs",
    )
    source = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default="running")
    step = models.CharField(max_length=200, blank=True, default="")
    cycles_done = models.PositiveIntegerField(default=0)
    max_cycles = models.PositiveIntegerField(default=3)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subject[:60]}: {self.status}"


class UsageRecord(models.Model):
    """Une réponse assistant : tokens, durée, route, succès. Jamais purgé (V1)."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="usage_records"
    )
    provider = models.ForeignKey(
        Provider, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    model_id = models.CharField(max_length=300, blank=True, default="")
    route = models.CharField(max_length=20, blank=True, default="")
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.model_id}: {self.total_tokens} tok"


class Export(models.Model):
    FORMATS = [(fmt, fmt.upper()) for fmt in ("pdf", "docx", "xlsx", "pptx")]
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="exports")
    format = models.CharField(max_length=4, choices=FORMATS)
    download_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file = models.FileField(upload_to="exports/%Y/%m/")
    file_name = models.CharField(max_length=160)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def get_absolute_url(self) -> str:
        return reverse("chat:export_download", kwargs={"token": self.download_token})


class ScheduledTask(models.Model):
    FREQUENCIES = [("daily", "Quotidien"), ("weekly", "Hebdomadaire"), ("monthly", "Mensuel"), ("once", "Une fois")]

    name = models.CharField(max_length=160)
    prompt = models.TextField()
    frequency = models.CharField(max_length=8, choices=FREQUENCIES, default="daily")
    days = models.CharField(max_length=32, blank=True, default="")
    month_day = models.PositiveSmallIntegerField(null=True, blank=True)
    time = models.TimeField(null=True, blank=True)
    once_at = models.DateTimeField(null=True, blank=True)
    provider = models.ForeignKey(
        Provider, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    model_id = models.CharField(max_length=300, blank=True, default="")
    temperature = models.FloatField(default=0.7)
    web_search = models.BooleanField(default=False)
    auto_export = models.CharField(max_length=4, blank=True, default="")
    enabled = models.BooleanField(default=True)
    conversation = models.OneToOneField(
        Conversation, null=True, blank=True, on_delete=models.CASCADE,
        related_name="scheduled_task",
    )
    failures = models.PositiveIntegerField(default=0)
    next_run = models.DateTimeField(null=True, blank=True)
    last_hash = models.CharField(max_length=64, blank=True, default="")
    last_excerpt = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_run", "name"]

    def __str__(self) -> str:
        return self.name


class ScheduledRun(models.Model):
    CAUSES = [
        ("due", "Échéance"),
        ("catchup", "Rattrapage"),
        ("manual", "Manuelle"),
        ("draft", "Brouillon"),
    ]

    task = models.ForeignKey(ScheduledTask, on_delete=models.CASCADE, related_name="runs")
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    seen = models.BooleanField(default=False)
    cause = models.CharField(max_length=8, choices=CAUSES, default="due")
    has_changes = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.task.name}: {'ok' if self.success else 'échec'}"


# Même expression côté client (voir scheduled_list.html) : {{nom}}.
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def template_variables(body: str) -> list[str]:
    """Variables {{…}} d'un gabarit, ordonnées et dédupliquées."""
    return list(dict.fromkeys(TEMPLATE_VAR_RE.findall(body or "")))


class TaskTemplate(models.Model):
    """Gabarit de consigne pour la création assistée de tâches planifiées."""

    name = models.CharField(max_length=160, unique=True)
    description = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField()
    suggest_web = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def variables(self) -> list[str]:
        return template_variables(self.body)

    def render(self, mapping: dict) -> str:
        """Substitue les variables. Stricte : variable manquante/vide → ValueError."""
        values = {str(k): str(v or "").strip() for k, v in (mapping or {}).items()}

        def _repl(match: re.Match) -> str:
            value = values.get(match.group(1), "")
            if not value:
                raise ValueError(f"Variable « {match.group(1)} » non renseignée.")
            return value

        rendered = TEMPLATE_VAR_RE.sub(_repl, self.body or "")
        if TEMPLATE_VAR_RE.search(rendered):
            raise ValueError("Il reste des variables non substituées.")
        return rendered
