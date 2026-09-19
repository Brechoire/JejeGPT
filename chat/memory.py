"""Mémoire inter-conversations : extraction auto + injection system."""
from __future__ import annotations

import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

MEMORY_SYSTEM = (
    "Tu retiens les faits durables sur l'utilisateur. Réponds UNIQUEMENT avec "
    "un objet JSON valide, sans markdown ni texte autour. "
    'Schéma : {"facts": ["..."]}. '
    "Faits durables uniquement : préférences, contexte stable, contraintes "
    "récurrentes. Rien d'éphémère (sujet du jour, météo). "
    'Liste vide {"facts": []} si rien de durable.'
)

_RE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def project_context(conv) -> str:
    """Contexte projet d'une conversation, '' si aucun."""
    project = getattr(conv, "project", None)
    if project is None or not (project.instructions or "").strip():
        return ""
    return f"Contexte projet {project.name} : {project.instructions.strip()}"


def memory_context() -> str:
    """Faits actifs (récents d'abord), tronqués au budget. '' si mémoire coupée/vide."""
    from .models import AppSettings, MemoryFact

    try:
        enabled = AppSettings.get_solo().memory_enabled
    except Exception:
        return ""
    if not enabled:
        return ""
    facts = list(
        MemoryFact.objects.filter(active=True)
        .order_by("-created_at")
        .values_list("content", flat=True)
    )
    if not facts:
        return ""
    budget = max(100, int(settings.JEJEGPT_MEMORY_MAX_CHARS))
    parts: list[str] = []
    total = 0
    for fact in facts:
        text = (fact or "").strip()
        if not text:
            continue
        total += len(text) + 2
        if total > budget and parts:
            break
        parts.append(text)
    if not parts:
        return ""
    return "Mémoire : " + " ; ".join(parts)


def _parse_facts(raw: str) -> list[str]:
    try:
        data = json.loads(_RE_FENCE.sub("", raw or "").strip())
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    facts = data.get("facts")
    if not isinstance(facts, list):
        return []
    cleaned = []
    for fact in facts:
        if isinstance(fact, str) and fact.strip() and len(fact.strip()) <= 300:
            text = fact.strip()
            if text not in cleaned:
                cleaned.append(text)
    return cleaned


def maybe_extract(conv, user_text: str, assistant_text: str) -> None:
    """Extrait et stocke les faits durables. Ne lève jamais, échec silencieux."""
    from . import providers
    from .models import AppSettings, MemoryFact

    try:
        if not AppSettings.get_solo().memory_enabled:
            return
        if conv.provider is None or not conv.model_id:
            return
        if not (assistant_text or "").strip():
            return
        raw = providers.chat_complete(
            conv.provider, conv.model_id,
            [
                {"role": "system", "content": MEMORY_SYSTEM},
                {"role": "user", "content": f"Question : {user_text or ''}\nRéponse : {assistant_text}"},
            ],
            temperature=0.0,
            timeout=settings.JEJEGPT_HTTP_TIMEOUT,
            route=providers.resolve_route(conv.provider, conv.model_id),
        )
        for text in _parse_facts(raw):
            if not MemoryFact.objects.filter(content=text).exists():
                MemoryFact.objects.create(content=text)
        max_facts = max(1, int(settings.JEJEGPT_MEMORY_MAX_FACTS))
        stale_ids = list(
            MemoryFact.objects.filter(active=True)
            .order_by("-created_at")
            .values_list("pk", flat=True)[max_facts:]
        )
        if stale_ids:
            MemoryFact.objects.filter(pk__in=stale_ids).update(active=False)
    except Exception:
        logger.exception("memory extract failed")
