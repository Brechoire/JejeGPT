import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from chat.scheduler.parse import (
    describe_schedule,
    needs_web_search,
    parse_schedule_request,
    validate_schedule_data,
)

pytestmark = pytest.mark.django_db

PARIS = ZoneInfo("Europe/Paris")


def _provider():
    from chat.models import Provider

    return Provider.objects.create(name="T", base_url="http://localhost:9/v1")


def test_parse_daily_ok(monkeypatch):
    from chat import providers as _pv

    payload = (
        '{"name": "Actus tech", "prompt": "Résume les actualités tech", '
        '"frequency": "daily", "days": [], "time": "10:00", "once_at": null}'
    )
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: payload)
    result = parse_schedule_request(_provider(), "m", "Résumé actus tous les jours à 10h")
    assert result is not None
    assert result["frequency"] == "daily"
    assert result["time"] == "10:00"
    assert result["days"] == []


def test_parse_weekly_et_fences(monkeypatch):
    from chat import providers as _pv

    payload = (
        '```json\n{"name": "Hebdo", "prompt": "Fais le point", "frequency": "weekly", '
        '"days": [0, 2], "time": "08:30", "once_at": null}\n```'
    )
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: payload)
    result = parse_schedule_request(_provider(), "m", "tous les lundis et mercredis à 8h30")
    assert result is not None
    assert result["days"] == [0, 2]


def test_parse_rejets():
    assert validate_schedule_data({"frequency": "daily", "name": "x", "prompt": "y"}) is None
    assert validate_schedule_data(
        {"frequency": "daily", "name": "x", "prompt": "y", "time": "25:00"}
    ) is None
    assert validate_schedule_data(
        {"frequency": "weekly", "name": "x", "prompt": "y", "time": "08:00", "days": []}
    ) is None
    assert validate_schedule_data(
        {"frequency": "weekly", "name": "x", "prompt": "y", "time": "08:00", "days": [7]}
    ) is None
    assert validate_schedule_data({"error": "pas de date"}) is None
    assert validate_schedule_data({"frequency": "daily"}) is None


def test_parse_reponse_invalide_et_erreur_provider(monkeypatch):
    from chat import providers as _pv

    provider = _provider()
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "désolé, pas de JSON ici")
    assert parse_schedule_request(provider, "m", "bla") is None

    def boom(*a, **k):
        raise _pv.ProviderError("coupure")

    monkeypatch.setattr(_pv, "chat_complete", boom)
    assert parse_schedule_request(provider, "m", "bla") is None
    assert parse_schedule_request(None, "", "bla") is None


def test_parse_once_futur_et_passe(monkeypatch):
    from chat import providers as _pv

    future = datetime.now(PARIS).replace(microsecond=0) + timedelta(days=2)
    payload = (
        '{"name": "Rappel", "prompt": "Rappelle", "frequency": "once", '
        f'"days": [], "time": null, "once_at": "{future.strftime("%Y-%m-%d %H:%M")}"}}'
    )
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: payload)
    result = parse_schedule_request(_provider(), "m", "rappelle-moi dans 2 jours")
    assert result is not None
    assert result["once_at"] == future.replace(second=0, microsecond=0, tzinfo=PARIS)

    past = (
        '{"name": "x", "prompt": "y", "frequency": "once", "days": [], '
        '"time": null, "once_at": "2000-01-01 10:00"}'
    )
    assert validate_schedule_data(json.loads(past)) is None


def test_needs_web_search():
    assert needs_web_search("Résumé des actualités tech")
    assert needs_web_search("les news du jour")
    assert not needs_web_search("résume ce document")
    assert not needs_web_search("")


def test_describe_schedule():
    assert describe_schedule("daily", [], "10:00") == "Tous les jours à 10:00"
    assert describe_schedule("weekly", [0, 2], "08:30") == "Chaque lundi, mercredi à 08:30"
    once = datetime(2026, 12, 25, 9, 0, tzinfo=PARIS)
    assert describe_schedule("once", [], None, once) == "Le 25/12/2026 à 09:00"


@pytest.mark.django_db
def test_provider_requis_pour_parser():
    assert parse_schedule_request(None, "", "tous les jours à 10h") is None
