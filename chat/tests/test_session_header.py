"""Régression : tous les appels LLM sortants portent session_id.

Le proxy Go (x-opencode-session) répond 400 sans ce header. Les chemins
auxiliaires (mémoire, parsing de planification) l'omettaient — cf. logs
« memory extract failed » (HTTP 400, Request is missing x-opencode-session).
"""
import pytest

from chat import providers
from chat.models import Conversation, Provider

pytestmark = pytest.mark.django_db


def test_maybe_extract_envoie_session_id(monkeypatch):
    from chat.memory import maybe_extract

    seen = {}
    monkeypatch.setattr(
        providers, "chat_complete",
        lambda *a, **k: (seen.update(k), '{"facts": []}')[1],
    )
    go = Provider.objects.get(name="OpenCode Go")
    conv = Conversation.objects.create(title="C", provider=go, model_id="m")
    maybe_extract(conv, "Q", "R")
    assert seen.get("session_id") == f"memory-{conv.pk}"


def test_parse_schedule_request_envoie_session_id(monkeypatch):
    from chat.scheduler.parse import parse_schedule_request

    seen = {}
    monkeypatch.setattr(
        providers, "chat_complete",
        lambda *a, **k: (seen.update(k), '{"error": "nope"}')[1],
    )
    go = Provider.objects.get(name="OpenCode Go")
    assert parse_schedule_request(go, "m", "tous les jours à 10h") is None
    assert seen.get("session_id") == f"schedule-parse-{go.pk}"


def test_headers_sans_session_id_omis():
    go = Provider.objects.get(name="OpenCode Go")
    assert "x-opencode-session" not in providers._headers(go)
    assert providers._headers(go, "memory-1")["x-opencode-session"] == "memory-1"
