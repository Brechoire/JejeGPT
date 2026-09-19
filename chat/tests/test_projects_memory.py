import pytest
from django.test import Client, override_settings
from django.urls import reverse

from chat.memory import memory_context, project_context
from chat.models import AppSettings, Conversation, MemoryFact, Project, Provider

pytestmark = pytest.mark.django_db

_SEQ = 0


def _provider():
    global _SEQ
    _SEQ += 1
    return Provider.objects.create(name=f"PM{_SEQ}", base_url="http://localhost:9/v1")


def _conversation(provider=None):
    return Conversation.objects.create(title="T", provider=provider, model_id="m")


# ---------------- Projets ----------------

def test_project_crud_et_conversations_conservees():
    client = Client()
    client.post(reverse("chat:project_create"), {"name": "SIGB"})
    project = Project.objects.get(name="SIGB")
    # Doublon : pas d'erreur, redirection vers l'existant.
    client.post(reverse("chat:project_create"), {"name": "SIGB"})
    assert Project.objects.count() == 1
    conv = _conversation()
    client.post(
        reverse("chat:conversation_update", args=[conv.pk]),
        {"project_id": str(project.pk)},
    )
    conv.refresh_from_db()
    assert conv.project_id == project.pk
    client.post(
        reverse("chat:project_update", args=[project.pk]),
        {"name": "SIGB+", "instructions": "Réponds en français."},
    )
    project.refresh_from_db()
    assert project.name == "SIGB+" and project.instructions == "Réponds en français."
    client.post(reverse("chat:project_delete", args=[project.pk]))
    assert Project.objects.count() == 0
    assert Conversation.objects.filter(pk=conv.pk).exists()  # conservée


def test_project_detail_et_nouvelle_conversation_dediee():
    project = Project.objects.create(name="Veille", instructions="Sois concis.")
    html = Client().get(reverse("chat:project_detail", args=[project.pk])).content.decode()
    assert "Sois concis." in html and "Nouvelle conversation dans ce projet" in html
    Client().post(reverse("chat:conversation_create"), {"project_id": str(project.pk)})
    conv = Conversation.objects.first()
    assert conv.project_id == project.pk


def test_sidebar_liste_projets():
    Project.objects.create(name="Alpha")
    conv = _conversation()
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "Projets" in html and "Alpha" in html


def test_history_injecte_projet_et_memoire():
    from chat.views import _history

    project = Project.objects.create(name="P", instructions="Contexte métier.")
    conv = _conversation()
    conv.system_prompt = "Base."
    conv.project = project
    conv.save()
    MemoryFact.objects.create(content="L'utilisateur préfère le français.")
    system = _history(conv)[0]["content"]
    assert "Base." in system
    assert "Contexte projet P : Contexte métier." in system
    assert "Mémoire :" in system and "préfère le français" in system


def test_scheduled_injecte_projet(monkeypatch):
    from chat import providers as _pv
    from chat.models import ScheduledRun, ScheduledTask
    from chat.scheduler.engine import run_task_now
    from datetime import time as dtime
    from django.utils import timezone

    sent = {}

    def fake_complete(provider, model_id, messages, **kwargs):
        sent["messages"] = messages
        return "ok"

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    provider = _provider()
    conv = _conversation(provider)
    conv.project = Project.objects.create(name="P", instructions="Contexte métier.")
    conv.save()
    task = ScheduledTask.objects.create(
        name="T", prompt="Go", frequency="daily", time=dtime(10, 0),
        provider=provider, model_id="m", conversation=conv, enabled=True,
        next_run=timezone.now() + timezone.timedelta(days=1),
    )
    run_task_now(task.pk)
    assert "Contexte projet P" in sent["messages"][0]["content"]
    assert ScheduledRun.objects.filter(task=task, success=True).exists()


# ---------------- Mémoire ----------------

def _extract_ok(*args, **kwargs):
    return '{"facts": ["L\'utilisateur aime Python."]}'


def test_extraction_stocke_faits(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", _extract_ok)
    conv = _conversation(_provider())
    from chat.views import _maybe_extract_memory

    _maybe_extract_memory(conv, "Quel langage ?", "Python bien sûr.")
    assert MemoryFact.objects.filter(content="L'utilisateur aime Python.").exists()
    # Doublon exact ignoré.
    _maybe_extract_memory(conv, "Et encore ?", "Python toujours.")
    assert MemoryFact.objects.count() == 1


def test_extraction_silencieuse_si_ephec_ou_vide(monkeypatch):
    from chat import providers as _pv

    def boom(*a, **k):
        raise _pv.ProviderError("coupure")

    monkeypatch.setattr(_pv, "chat_complete", boom)
    conv = _conversation(_provider())
    from chat.views import _maybe_extract_memory

    _maybe_extract_memory(conv, "Q", "R")  # ne lève pas
    assert MemoryFact.objects.count() == 0
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: '{"facts": []}')
    _maybe_extract_memory(conv, "Q", "R")
    assert MemoryFact.objects.count() == 0
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "pas du json")
    _maybe_extract_memory(conv, "Q", "R")
    assert MemoryFact.objects.count() == 0


def test_extraction_coupee_si_desactivee(monkeypatch):
    from chat import providers as _pv

    calls = []
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: calls.append(1) or '{"facts": ["x"]}')
    solo = AppSettings.get_solo()
    solo.memory_enabled = False
    solo.save(update_fields=["memory_enabled"])
    conv = _conversation(_provider())
    from chat.views import _maybe_extract_memory

    _maybe_extract_memory(conv, "Q", "R")
    assert calls == [] and MemoryFact.objects.count() == 0
    assert memory_context() == ""


@override_settings(JEJEGPT_MEMORY_MAX_FACTS=2)
def test_plafond_desactive_anciens(monkeypatch):
    from chat import providers as _pv

    states = [
        '{"facts": ["fait un"]}',
        '{"facts": ["fait deux"]}',
        '{"facts": ["fait trois"]}',
    ]
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: states.pop(0))
    conv = _conversation(_provider())
    from chat.views import _maybe_extract_memory

    for _ in range(3):
        _maybe_extract_memory(conv, "Q", "R")
    assert MemoryFact.objects.filter(active=True).count() == 2
    assert not MemoryFact.objects.get(content="fait un").active


def test_memory_toggle_delete_et_interrupteur():
    fact = MemoryFact.objects.create(content="F1")
    client = Client()
    client.post(reverse("chat:memory_toggle", args=[fact.pk]))
    fact.refresh_from_db()
    assert fact.active is False
    client.post(reverse("chat:memory_delete", args=[fact.pk]))
    assert MemoryFact.objects.count() == 0
    client.post(reverse("chat:settings_save"), {"memory_enabled": "1"})
    assert AppSettings.get_solo().memory_enabled is True
    html = client.get(reverse("chat:settings_tab", kwargs={"tab": "personnalisation"})).content.decode()
    assert "Mémoire" in html


def test_project_context_vide_sans_projet_ni_instructions():
    conv = _conversation()
    assert project_context(conv) == ""
    conv.project = Project.objects.create(name="Vide")
    assert project_context(conv) == ""
