from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

from chat.scheduler.engine import (
    compute_next_run,
    dtime_from_str,
    parse_days,
    run_due_tasks,
    scheduler_should_run,
    start_scheduler,
)

PARIS = ZoneInfo("Europe/Paris")

pytestmark = pytest.mark.django_db


def _aware(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=PARIS)


_PROVIDER_SEQ = 0


def _task(**kwargs):
    from chat.models import Conversation, Provider, ScheduledTask

    global _PROVIDER_SEQ
    _PROVIDER_SEQ += 1
    provider = Provider.objects.create(name=f"T{_PROVIDER_SEQ}", base_url="http://localhost:9/v1")
    conv = Conversation.objects.create(title="Plan", provider=provider, model_id="m")
    defaults = {
        "name": "Actus", "prompt": "Résume", "frequency": "daily",
        "time": dtime(10, 0), "provider": provider, "model_id": "m",
        "conversation": conv, "enabled": True,
    }
    defaults.update(kwargs)
    return ScheduledTask.objects.create(**defaults)


def test_parse_days():
    assert parse_days("1,3,5") == [1, 3, 5]
    assert parse_days([0, 2, 2]) == [0, 2]
    assert parse_days("") == []
    assert parse_days(None) == []
    assert parse_days("9,abc,2") == [2]


def test_compute_daily():
    monday_9h = _aware(2026, 9, 21, 9, 0)
    assert compute_next_run("daily", [], "10:00", None, monday_9h) == _aware(2026, 9, 21, 10, 0)
    monday_11h = _aware(2026, 9, 21, 11, 0)
    assert compute_next_run("daily", [], "10:00", None, monday_11h) == _aware(2026, 9, 22, 10, 0)
    assert compute_next_run("daily", [], dtime(10, 0), None, monday_9h) == _aware(2026, 9, 21, 10, 0)


def test_compute_weekly():
    monday_9h = _aware(2026, 9, 21, 9, 0)
    assert compute_next_run("weekly", "0,2", "08:30", None, monday_9h) == _aware(2026, 9, 23, 8, 30)
    monday_7h = _aware(2026, 9, 21, 7, 0)
    assert compute_next_run("weekly", "0,2", "08:30", None, monday_7h) == _aware(2026, 9, 21, 8, 30)
    assert compute_next_run("weekly", "", "08:30", None, monday_7h) is None


def test_compute_once():
    now = _aware(2026, 9, 21, 9, 0)
    future = _aware(2026, 9, 25, 10, 0)
    assert compute_next_run("once", [], None, future, now) == future
    assert compute_next_run("once", [], None, _aware(2026, 9, 20, 10, 0), now) is None
    assert compute_next_run("once", [], None, None, now) is None


def test_run_due_succes_et_avance_next(monkeypatch):
    from chat import providers as _pv
    from chat.models import Message, ScheduledRun

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Résumé du jour.")
    task = _task(next_run=_aware(2026, 9, 21, 10, 0))
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 1
    task.refresh_from_db()
    assert task.next_run == _aware(2026, 9, 22, 10, 0)
    assert task.failures == 0
    run = ScheduledRun.objects.get(task=task)
    assert run.success is True and run.seen is False
    assert Message.objects.filter(conversation=task.conversation, content="Résumé du jour.").exists()


def test_run_ignore_non_du_et_desactive(monkeypatch):
    from chat import providers as _pv

    calls = []
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: calls.append(1) or "x")
    _task(next_run=_aware(2026, 9, 22, 10, 0))
    _task(next_run=_aware(2026, 9, 21, 10, 0), enabled=False)
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 0
    assert calls == []


def test_run_echec_puis_auto_disable(monkeypatch):
    from chat import providers as _pv
    from chat.models import ScheduledRun

    def boom(*a, **k):
        raise _pv.ProviderError("coupure")

    monkeypatch.setattr(_pv, "chat_complete", boom)
    task = _task(next_run=_aware(2026, 9, 21, 10, 0))
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 1
    task.refresh_from_db()
    assert task.failures == 1 and task.enabled is True
    assert ScheduledRun.objects.get(task=task).success is False
    task.next_run = _aware(2026, 9, 21, 10, 0)
    task.save(update_fields=["next_run"])
    task.failures = 2
    task.save(update_fields=["failures"])
    run_due_tasks(now=_aware(2026, 9, 21, 10, 1))
    task.refresh_from_db()
    assert task.failures == 3 and task.enabled is False


def test_run_sans_provider_enregistre_echec(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "x")
    task = _task(next_run=_aware(2026, 9, 21, 10, 0), provider=None, model_id="")
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 1
    task.refresh_from_db()
    assert task.failures == 1


def test_run_idempotent_double_tick(monkeypatch):
    from chat import providers as _pv
    from chat.models import ScheduledRun

    calls = []
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: calls.append(1) or "x")
    _task(next_run=_aware(2026, 9, 21, 10, 0))
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 1
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 0
    assert len(calls) == 1
    assert ScheduledRun.objects.count() == 1


def test_run_once_passe_desactive_sans_executer(monkeypatch):
    from chat import providers as _pv
    from chat.models import ScheduledRun

    calls = []
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: calls.append(1) or "x")
    task = _task(frequency="once", time=None, once_at=_aware(2026, 9, 20, 10, 0),
                 next_run=_aware(2026, 9, 20, 10, 0))
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 0
    task.refresh_from_db()
    assert task.enabled is False and task.next_run is None
    assert calls == []
    assert ScheduledRun.objects.count() == 0


def test_run_exception_inattendue(monkeypatch):
    from chat import providers as _pv

    def crash(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(_pv, "chat_complete", crash)
    task = _task(next_run=_aware(2026, 9, 21, 10, 0))
    assert run_due_tasks(now=_aware(2026, 9, 21, 10, 1)) == 1
    task.refresh_from_db()
    assert task.failures == 1 and task.enabled is True


def test_scheduler_should_run_guards(monkeypatch):
    assert scheduler_should_run(["manage.py", "migrate"]) is False
    assert scheduler_should_run(["manage.py", "runserver"]) is False
    assert scheduler_should_run(["pytest"]) is False
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert scheduler_should_run(["manage.py", "runserver", "--noreload"]) is True
    monkeypatch.setenv("RUN_MAIN", "true")
    assert scheduler_should_run(["manage.py", "runserver"]) is True


def test_start_scheduler_idempotent(monkeypatch):
    import chat.scheduler.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda s: (_ for _ in ()).throw(SystemExit))
    assert start_scheduler() is True
    assert start_scheduler() is False


def test_dtime_from_str():
    assert dtime_from_str("10:00") == dtime(10, 0)


def test_execute_ajoute_consignes_sources_si_web(monkeypatch):
    from chat import providers as _pv
    from chat.models import Message
    from chat.scheduler.engine import _execute

    sent = {}

    def fake_complete(provider, model_id, messages, **kwargs):
        sent["messages"] = messages
        return "ok"

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    task = _task(next_run=_aware(2026, 9, 21, 10, 0), web_search=True)
    _execute(task, _aware(2026, 9, 21, 10, 1))
    user_content = sent["messages"][-1]["content"]
    assert "Sources" in user_content and "[nom](https://" in user_content
    assert Message.objects.filter(conversation=task.conversation, content="ok").exists()


def test_execute_sans_consignes_sources_si_pas_web(monkeypatch):
    from chat import providers as _pv
    from chat.scheduler.engine import _execute

    sent = {}

    def fake_complete(provider, model_id, messages, **kwargs):
        sent["messages"] = messages
        return "ok"

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    task = _task(next_run=_aware(2026, 9, 21, 10, 0), web_search=False)
    _execute(task, _aware(2026, 9, 21, 10, 1))
    assert sent["messages"][-1]["content"] == "Résume"
