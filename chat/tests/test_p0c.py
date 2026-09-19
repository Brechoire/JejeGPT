from datetime import time as dtime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from chat.models import Conversation, Provider, ScheduledRun, ScheduledTask
from chat.scheduler.engine import (
    _normalize_reply,
    _reply_hash,
    compute_next_run,
    run_due_tasks,
    run_task_now,
)
from chat.scheduler.parse import describe_schedule, validate_schedule_data

pytestmark = pytest.mark.django_db

_SEQ = 0


def _provider():
    global _SEQ
    _SEQ += 1
    return Provider.objects.create(name=f"PC{_SEQ}", base_url="http://localhost:9/v1")


def _task(**kwargs):
    provider = _provider()
    conv = Conversation.objects.create(title="T", provider=provider, model_id="m")
    defaults = {
        "name": "T", "prompt": "Go", "frequency": "daily",
        "time": dtime(10, 0), "provider": provider, "model_id": "m",
        "conversation": conv, "enabled": True,
        "next_run": timezone.now() + timezone.timedelta(days=1),
    }
    defaults.update(kwargs)
    return ScheduledTask.objects.create(**defaults)


def _paris(*args):
    from zoneinfo import ZoneInfo

    return timezone.datetime(*args, tzinfo=ZoneInfo("Europe/Paris"))


# ---------------- Mensuel ----------------

def test_compute_monthly():
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 10, 9, 0), month_day=15) == _paris(2026, 9, 15, 10, 0)
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 16, 9, 0), month_day=15) == _paris(2026, 10, 15, 10, 0)
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 12, 20, 9, 0), month_day=15) == _paris(2027, 1, 15, 10, 0)
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 10, 9, 0), month_day=None) is None
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 10, 9, 0), month_day=0) is None
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 10, 9, 0), month_day=29) is None
    assert compute_next_run("monthly", [], "10:00", None, _paris(2026, 9, 10, 9, 0), month_day="15") is None
    assert compute_next_run("monthly", [], None, None, _paris(2026, 9, 10, 9, 0), month_day=15) is None


def test_validate_monthly():
    base = {"name": "M", "prompt": "P", "frequency": "monthly", "time": "08:30", "month_day": 15}
    assert validate_schedule_data(base)["month_day"] == 15
    assert validate_schedule_data({**base, "month_day": "15"})["month_day"] == 15
    assert validate_schedule_data({**base, "month_day": None}) is None
    assert validate_schedule_data({**base, "month_day": 29}) is None
    assert validate_schedule_data({**base, "month_day": 0}) is None
    assert describe_schedule("monthly", [], "08:30", None, month_day=15) == "Chaque 15 du mois à 08:30"


def test_create_mensuelle_via_vue():
    provider = _provider()
    client = Client()
    response = client.post(reverse("chat:scheduled_create"), {
        "name": "Mensu", "prompt": "Bilan", "frequency": "monthly",
        "month_day": "15", "time": "08:30", "provider": str(provider.pk),
        "model_id": "m", "enabled": "1",
    })
    assert response.status_code == 302
    task = ScheduledTask.objects.get(name="Mensu")
    assert task.month_day == 15 and task.next_run.day == 15
    assert "Chaque 15 du mois" in client.get(reverse("chat:scheduled_list")).content.decode()
    # Sans jour : refusée.
    response = client.post(reverse("chat:scheduled_create"), {
        "name": "Bad", "prompt": "Bilan", "frequency": "monthly",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
    })
    assert not ScheduledTask.objects.filter(name="Bad").exists()


def test_hook_chat_mensuel(monkeypatch):
    from chat import providers as _pv
    from chat.models import Message

    def fake_complete(provider, model_id, messages, **kwargs):
        if any("extracteur de planification" in str(m.get("content", "")) for m in messages):
            return (
                '{"name": "Bilan", "prompt": "Fais le bilan", '
                '"frequency": "monthly", "days": [], "month_day": 5, '
                '"time": "09:00", "once_at": null}'
            )
        return "ok"

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    provider = _provider()
    conv = Conversation.objects.create(title="T", provider=provider, model_id="m")
    Client().post(
        f"/conversations/{conv.pk}/send/",
        {"content": "Fais-moi un bilan le 5 de chaque mois à 9h"},
        HTTP_HX_REQUEST="true",
    )
    task = ScheduledTask.objects.get()
    assert task.frequency == "monthly" and task.month_day == 5
    assert "Chaque 5 du mois" in Message.objects.filter(
        conversation=conv, role="assistant"
    ).last().content


# ---------------- Rattrapage ----------------

def test_rattrapage_unique_marqué(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Contenu.")
    task = _task(next_run=timezone.now() - timezone.timedelta(hours=2))
    assert run_due_tasks() == 1
    task.refresh_from_db()
    run = ScheduledRun.objects.get(task=task)
    assert run.cause == "catchup" and run.success is True
    assert task.next_run > timezone.now()
    msg = task.conversation.messages.filter(role="assistant").last()
    assert msg.content.startswith("🔁 Rattrapage de l'échéance du ")
    # Pas d'avalanche : un 2e tick ne relance rien.
    assert run_due_tasks() == 0


def test_leger_retard_cause_due(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Contenu.")
    task = _task(next_run=timezone.now() - timezone.timedelta(seconds=30))
    assert run_due_tasks() == 1
    assert ScheduledRun.objects.get(task=task).cause == "due"


def test_causes_manual_et_draft(monkeypatch):
    from chat import providers as _pv
    from chat.scheduler.engine import run_task_draft

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Contenu.")
    task = _task()
    run_task_now(task.pk)
    assert ScheduledRun.objects.filter(task=task, cause="manual").exists()
    run_task_draft(task.pk)
    assert ScheduledRun.objects.filter(task=task, cause="draft").exists()


# ---------------- Monitoring ----------------

def test_normalisation_insensible_casse_espaces():
    assert _reply_hash("Hello  World") == _reply_hash("  hello\nworld ")
    assert _normalize_reply("  A\tB ") == "a b"


def test_runs_identiques_sans_changement(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Veille du jour.")
    task = _task(next_run=timezone.now() - timezone.timedelta(seconds=10))
    run_due_tasks()
    first = ScheduledRun.objects.get(task=task)
    assert first.has_changes is True
    task.refresh_from_db()
    assert task.last_hash != "" and "Veille" in task.last_excerpt
    task.next_run = timezone.now() - timezone.timedelta(seconds=10)
    task.save(update_fields=["next_run"])
    run_due_tasks()
    runs = list(ScheduledRun.objects.filter(task=task).order_by("created_at"))
    assert len(runs) == 2 and runs[1].has_changes is False
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "sans changement" in html


def test_contenu_modifie_change_detecte(monkeypatch):
    from chat import providers as _pv

    answers = ["Version un.", "Version deux."]
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: answers.pop(0))
    task = _task(next_run=timezone.now() - timezone.timedelta(seconds=10))
    run_due_tasks()
    task.next_run = timezone.now() - timezone.timedelta(seconds=10)
    task.save(update_fields=["next_run"])
    run_due_tasks()
    runs = list(ScheduledRun.objects.filter(task=task).order_by("created_at"))
    assert all(r.has_changes for r in runs)
    task.refresh_from_db()
    assert "deux" in task.last_excerpt
