from datetime import time as dtime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from chat.models import Conversation, Message, Provider, ScheduledRun, ScheduledTask

pytestmark = pytest.mark.django_db

_SEQ = 0


def _provider(name=None):
    global _SEQ
    _SEQ += 1
    return Provider.objects.create(name=name or f"P{_SEQ}", base_url="http://localhost:9/v1")


def _conversation(provider=None, model_id="m"):
    return Conversation.objects.create(title="T", provider=provider, model_id=model_id)


def _task(**kwargs):
    provider = _provider()
    conv = _conversation(provider)
    defaults = {
        "name": "Actus", "prompt": "Résume", "frequency": "daily",
        "time": dtime(10, 0), "provider": provider, "model_id": "m",
        "conversation": conv, "enabled": True,
        "next_run": timezone.now() + timezone.timedelta(days=1),
    }
    defaults.update(kwargs)
    return ScheduledTask.objects.create(**defaults)


def test_liste_rend_taches_et_formulaire():
    _task(name="Actus tech")
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "Actus tech" in html
    assert "Nouvelle tâche" in html
    assert "Planifiées" in html


def test_formulaire_liste_modeles_du_provider():
    from chat.models import LLMModel

    provider = _provider()
    LLMModel.objects.create(provider=provider, model_id="glim-1", display_name="Glim 1")
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert 'id="sched-model-select"' in html
    assert "glim-1" in html and "Glim 1" in html
    assert "Autre (saisie libre)" in html


def test_create_manuelle_ok():
    provider = _provider()
    response = Client().post(reverse("chat:scheduled_create"), {
        "name": "Hebdo", "prompt": "Fais le point", "frequency": "weekly",
        "days": ["0", "2"], "time": "08:30", "provider": str(provider.pk),
        "model_id": "m", "enabled": "1",
    })
    assert response.status_code == 302
    task = ScheduledTask.objects.get(name="Hebdo")
    assert task.days == "0,2" and task.enabled is True
    assert task.next_run is not None and task.conversation is not None


def test_create_invalide_refusee():
    provider = _provider()
    response = Client().post(reverse("chat:scheduled_create"), {
        "name": "X", "prompt": "Y", "frequency": "weekly",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
    })
    assert response.status_code == 302
    assert ScheduledTask.objects.count() == 0


def test_toggle_et_run_now_et_delete(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Résumé.")
    task = _task()
    client = Client()
    client.post(reverse("chat:scheduled_toggle", args=[task.pk]))
    task.refresh_from_db()
    assert task.enabled is False
    client.post(reverse("chat:scheduled_toggle", args=[task.pk]))
    task.refresh_from_db()
    assert task.enabled is True and task.next_run is not None
    client.post(reverse("chat:scheduled_run_now", args=[task.pk]))
    assert ScheduledRun.objects.filter(task=task, success=True).exists()
    client.post(reverse("chat:scheduled_delete", args=[task.pk]))
    assert ScheduledTask.objects.count() == 0


def test_sidebar_badge_non_lu_et_detail_marque_lu():
    task = _task()
    ScheduledRun.objects.create(task=task, success=True, seen=False)
    conv = _conversation()
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "Planifiées" in html
    assert "bg-accent" in html
    Client().get(f"/{task.conversation.pk}/")
    assert ScheduledRun.objects.filter(seen=False).count() == 0


def test_hook_chat_cree_tache_en_pause(monkeypatch):
    from chat import providers as _pv

    calls = []

    def fake_complete(provider, model_id, messages, **kwargs):
        calls.append(messages)
        if any("extracteur de planification" in str(m.get("content", "")) for m in messages):
            return (
                '{"name": "Actus tech", "prompt": "Résume les actus", '
                '"frequency": "daily", "days": [], "time": "10:00", "once_at": null}'
            )
        return "Voici un résumé."

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    provider = _provider()
    conv = _conversation(provider)
    response = Client().post(
        f"/conversations/{conv.pk}/send/",
        {"content": "Fais-moi un résumé des actualités tech tous les jours à 10h"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    task = ScheduledTask.objects.get()
    assert task.enabled is False
    assert task.conversation is not None and task.next_run is not None
    assert "Planifiées" in response.content.decode()


def test_hook_chat_parse_incompris_sans_tache(monkeypatch):
    from chat import providers as _pv

    def fake_complete(provider, model_id, messages, **kwargs):
        if any("extracteur de planification" in str(m.get("content", "")) for m in messages):
            return '{"error": "pas de date"}'
        return "ok"

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    provider = _provider()
    conv = _conversation(provider)
    Client().post(
        f"/conversations/{conv.pk}/send/",
        {"content": "Rappelle-moi ça tous les jours"},
        HTTP_HX_REQUEST="true",
    )
    assert ScheduledTask.objects.count() == 0
    assert "Je n'ai pas compris la planification" in Message.objects.filter(
        conversation=conv, role="assistant"
    ).last().content


def test_hook_chat_sans_intention_silencieux(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "pong")
    provider = _provider()
    conv = _conversation(provider)
    before = Message.objects.count()
    Client().post(
        f"/conversations/{conv.pk}/send/", {"content": "ping"}, HTTP_HX_REQUEST="true"
    )
    assert Message.objects.count() == before + 2
    assert ScheduledTask.objects.count() == 0


def test_reset_failures_sans_changer_etat():
    task = _task(failures=2, enabled=False)
    Client().post(reverse("chat:scheduled_reset_failures", args=[task.pk]))
    task.refresh_from_db()
    assert task.failures == 0 and task.enabled is False


def test_duplicate_isolee_en_pause():
    task = _task(name="Veille", failures=1, enabled=True)
    ScheduledRun.objects.create(task=task, success=True)
    client = Client()
    client.post(reverse("chat:scheduled_duplicate", args=[task.pk]))
    assert ScheduledTask.objects.count() == 2
    copy = ScheduledTask.objects.exclude(pk=task.pk).get()
    assert copy.name == "Veille (copie)"
    assert copy.enabled is False and copy.failures == 0
    assert copy.prompt == task.prompt and copy.frequency == task.frequency
    assert copy.conversation_id != task.conversation_id
    assert copy.runs.count() == 0
    assert ScheduledRun.objects.filter(task=task).count() == 1


def test_draft_sans_effet_planification(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Brouillon ok.")
    before_next = timezone.now() + timezone.timedelta(days=1)
    task = _task(enabled=False, failures=2, next_run=before_next)
    Client().post(reverse("chat:scheduled_draft", args=[task.pk]))
    task.refresh_from_db()
    assert task.enabled is False and task.failures == 2 and task.next_run == before_next
    assert ScheduledRun.objects.filter(task=task, success=True).exists()
    assert Message.objects.filter(conversation=task.conversation, content="Brouillon ok.").exists()


def test_auto_export_uniquement_si_option_et_succes(monkeypatch):
    from chat import providers as _pv
    from chat.models import Export

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Contenu à exporter.")
    client = Client()
    avec = _task(next_run=timezone.now() + timezone.timedelta(days=1), auto_export="pdf")
    sans = _task(next_run=timezone.now() + timezone.timedelta(days=1))
    client.post(reverse("chat:scheduled_run_now", args=[avec.pk]))
    client.post(reverse("chat:scheduled_run_now", args=[sans.pk]))
    assert Export.objects.filter(message__conversation=avec.conversation, format="pdf").exists()
    assert not Export.objects.filter(message__conversation=sans.conversation).exists()


def test_liste_historique_boutons_et_aide_dynamique():
    from django.conf import settings as _s

    task = _task(name="Veille", failures=1)
    msg = Message.objects.create(conversation=task.conversation, role="assistant", content="Résultat.")
    ScheduledRun.objects.create(task=task, message=msg, success=True)
    ScheduledRun.objects.create(task=task, message=None, success=False, error="coupure")
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert f"{_s.JEJEGPT_SCHEDULER_MAX_FAILURES} échecs de suite" in html
    assert "Dernier échec" in html and "coupure" in html
    assert "Réessayer" in html and "Échecs à zéro" in html
    assert "Dupliquer" in html and "Brouillon" in html
    assert "Historique" in html and f"#msg-{msg.pk}" in html
    assert 'id="task-filter"' in html and 'id="task-sort"' in html
    assert "Modèles :" in html and "Export auto" in html


def test_thread_ancre_message():
    conv = _conversation()
    msg = Message.objects.create(conversation=conv, role="assistant", content="Hello.")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert f'id="msg-{msg.pk}"' in html
