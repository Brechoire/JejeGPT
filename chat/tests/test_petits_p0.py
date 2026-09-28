from datetime import time as dtime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from chat.models import Conversation, Provider, ScheduledRun, ScheduledTask
from chat.scheduler.engine import check_triggers, parse_sites, sites_suffix, trigger_url_allowed

pytestmark = pytest.mark.django_db

_SEQ = 0


def _provider():
    global _SEQ
    _SEQ += 1
    return Provider.objects.create(name=f"PP{_SEQ}", base_url="http://localhost:9/v1")


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


# ---------------- Surveillance ----------------

def test_trigger_url_allowed():
    assert trigger_url_allowed("https://example.com/page")
    assert trigger_url_allowed("http://example.com:8080/a?b=1")
    assert not trigger_url_allowed("http://localhost:3000/x")
    assert not trigger_url_allowed("http://127.0.0.1/")
    assert not trigger_url_allowed("http://10.0.0.5/")
    assert not trigger_url_allowed("http://192.168.1.1/")
    assert not trigger_url_allowed("http://169.254.169.254/")
    assert not trigger_url_allowed("ftp://example.com/f")
    assert not trigger_url_allowed("example.com")
    assert not trigger_url_allowed("")


def test_trigger_change_baseline_puis_feu(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Analyse.")
    texts = ["contenu identique"]
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text", lambda url: texts[0]
    )
    task = _task(trigger_url="https://example.com/prix")
    assert check_triggers() == 0  # 1er passage : référence silencieuse
    assert ScheduledRun.objects.count() == 0
    assert check_triggers() == 0  # inchangé : rien
    task.refresh_from_db()
    assert task.last_trigger_hash != ""
    texts[0] = "contenu modifié"
    task.last_check = timezone.now() - timezone.timedelta(minutes=20)
    task.save(update_fields=["last_check"])
    assert check_triggers() == 1
    run = ScheduledRun.objects.get(task=task)
    assert run.cause == "trigger" and run.success is True


def test_trigger_keyword_absent_present(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Analyse.")
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text",
        lambda url: "rien d'intéressant ici",
    )
    task = _task(trigger_url="https://example.com/blog", trigger_mode="keyword", trigger_keyword="solde")
    assert check_triggers() == 0
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text",
        lambda url: "grandes soldes d'hiver",
    )
    task.last_check = timezone.now() - timezone.timedelta(minutes=20)
    task.save(update_fields=["last_check"])
    assert check_triggers() == 1
    assert ScheduledRun.objects.get(task=task).cause == "trigger"


def test_trigger_intervalle_min_et_url_refusee_vue(monkeypatch):
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text", lambda url: "x"
    )
    task = _task(trigger_url="https://example.com/a")
    task.last_check = timezone.now()
    task.save(update_fields=["last_check"])
    assert check_triggers() == 0  # trop récent : sauté
    provider = _provider()
    response = Client().post(reverse("chat:scheduled_create"), {
        "name": "Bad", "prompt": "P", "frequency": "daily",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
        "trigger_url": "http://localhost:9/secret",
    })
    assert response.status_code == 302
    assert not ScheduledTask.objects.filter(name="Bad").exists()
    response = Client().post(reverse("chat:scheduled_create"), {
        "name": "Kw", "prompt": "P", "frequency": "daily",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
        "trigger_url": "https://example.com/b", "trigger_mode": "keyword",
        "trigger_keyword": "",
    })
    assert not ScheduledTask.objects.filter(name="Kw").exists()


def test_trigger_echecs_fetch_desactivent(monkeypatch):
    def boom(url):
        raise ConnectionError("down")

    monkeypatch.setattr("chat.scheduler.engine._fetch_trigger_text", boom)
    task = _task(trigger_url="https://example.com/c", failures=2)
    check_triggers()
    task.refresh_from_db()
    assert task.failures == 3 and task.enabled is False
    assert ScheduledRun.objects.count() == 0


def test_historique_affiche_declenchement(monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "A.")
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text", lambda url: "v2"
    )
    task = _task(trigger_url="https://example.com/d")
    check_triggers()  # baseline
    task.last_check = timezone.now() - timezone.timedelta(minutes=20)
    task.save(update_fields=["last_check"])
    monkeypatch.setattr(
        "chat.scheduler.engine._fetch_trigger_text", lambda url: "v3"
    )
    check_triggers()
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "déclenchement" in html


# ---------------- Sites restreints ----------------

def test_parse_sites():
    domains, error = parse_sites("lemonde.fr\nExemple.COM\nlemonde.fr\n")
    assert (domains, error) == (["lemonde.fr", "exemple.com"], "")
    assert parse_sites("")[0] == []
    assert parse_sites("https://example.com/x")[1] != ""
    assert parse_sites("www.example.com")[1] != ""
    assert parse_sites("pas un domaine")[1] != ""
    assert parse_sites("a,b")[1] != ""
    assert parse_sites("\n".join(f"d{i}.fr" for i in range(11)))[1] != ""
    suffix = sites_suffix("lemonde.fr\nexample.com")
    assert "site:lemonde.fr" in suffix and "Limite tes sources" in suffix
    assert sites_suffix("") == ""


def test_create_sites_et_injection(monkeypatch):
    from chat import providers as _pv

    sent = {}
    monkeypatch.setattr(
        _pv, "chat_complete",
        lambda *a, **k: sent.setdefault("user", a[2][1]["content"]) or "ok",
    )
    provider = _provider()
    client = Client()
    client.post(reverse("chat:scheduled_create"), {
        "name": "Actus", "prompt": "Résume", "frequency": "daily",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
        "enabled": "1", "sites": "lemonde.fr\nnota Bene",
    })
    assert not ScheduledTask.objects.filter(name="Actus").exists()  # ligne invalide
    client.post(reverse("chat:scheduled_create"), {
        "name": "Actus", "prompt": "Résume", "frequency": "daily",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
        "enabled": "1", "sites": "LeMonde.fr\nexample.com",
    })
    task = ScheduledTask.objects.get(name="Actus")
    assert task.sites == "lemonde.fr\nexample.com"
    client.post(reverse("chat:scheduled_run_now", args=[task.pk]))
    assert "site:lemonde.fr" in sent["user"] and "Limite tes sources" in sent["user"]


# ---------------- Tendances ----------------

def _usage(provider, conv, day_offset, total, hour=10, success=True):
    from chat.models import UsageRecord

    rec = UsageRecord.objects.create(
        conversation=conv, provider=provider, model_id="m", total_tokens=total,
        prompt_tokens=1, completion_tokens=total - 1, duration_ms=1000, success=success,
    )
    created = timezone.now() - timezone.timedelta(days=day_offset)
    UsageRecord.objects.filter(pk=rec.pk).update(created_at=created.replace(hour=hour))
    return rec


def test_tendances_agregations():
    from chat.models import Message, UsageRecord

    provider = _provider()
    conv_a = Conversation.objects.create(title="Alpha", provider=provider, model_id="m")
    conv_b = Conversation.objects.create(title="Beta", provider=provider, model_id="m")
    _usage(provider, conv_a, 0, 100, hour=10)
    _usage(provider, conv_a, 0, 50, hour=10)
    _usage(provider, conv_b, 1, 1000, hour=14)
    Message.objects.create(conversation=conv_a, role="assistant", content="a", feedback="up")
    Message.objects.create(conversation=conv_a, role="assistant", content="b", feedback="down")
    Message.objects.create(conversation=conv_a, role="assistant", content="c")
    html = Client().get(reverse("chat:settings_tab", kwargs={"tab": "utilisation"}) + "?period=7").content.decode()
    assert "Tendances" in html
    from django.db.models import Count
    from django.db.models.functions import ExtractHour

    from chat.models import UsageRecord

    peaks = {
        r["hour"]: r["calls"]
        for r in UsageRecord.objects.annotate(hour=ExtractHour("created_at"))
        .values("hour").annotate(calls=Count("id"))
    }
    top_hour = max(peaks, key=peaks.get)
    assert f"{top_hour:02d}h — {peaks[top_hour]} appel(s)" in html  # heure de pointe
    assert "Beta" in html  # top conversations (1000 tokens)
    assert "👍 1 · 👎 1 · sur 3 réponses" in html


# ---------------- Seeds Quiz / Infographie ----------------

def test_seeds_quiz_infographie():
    from chat.models import TaskTemplate

    quiz = TaskTemplate.objects.get(name="Quiz interactif")
    assert quiz.variables() == ["sujet", "nombre"]
    assert "Voir la réponse" in quiz.render({"sujet": "Python", "nombre": "3"})
    fiche = TaskTemplate.objects.get(name="Fiche infographie")
    assert fiche.variables() == ["sujet"]
    assert "non disponible" in fiche.render({"sujet": "IA"})
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "Quiz interactif" in html and "Fiche infographie" in html
