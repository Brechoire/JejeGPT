"""Bouton « Test Web » : verdicts, persistance, non-modification des cases.

Aucun vrai réseau : chat_complete et httpx mockés. Les garde-fous vérifiés :
sémantique (cases intactes), session_id Go, verdicts ok/ko/doute.
"""
import pytest
from django.test import Client

from chat import providers
from chat.models import LLMModel, Provider
from chat.webtest import build_probe, extract_links, judge, run_web_test, verify_links

pytestmark = pytest.mark.django_db


def _go():
    return Provider.objects.get(name="OpenCode Go")


def test_build_probe_datee():
    probe = build_probe("2026-09-29")
    assert "2026-09-29" in probe and "2 derniers" in probe


def test_extract_links_dedup():
    text = "[A](https://a.fr/x) et [A](https://a.fr/x) puis [B](https://b.fr/y)."
    assert extract_links(text) == ["https://a.fr/x", "https://b.fr/y"]


def test_judge_refus_ko():
    status, _ = judge("Je ne peux pas naviguer sur internet en temps réel.", 0, 0)
    assert status == "ko"


def test_judge_sans_lien_doute():
    status, _ = judge("Voici les actus : IA, robots, puces.", 0, 0)
    assert status == "doute"


def test_judge_liens_verifies_ok():
    reply = "Actus : [X](https://x.fr/a)\n\nSources : [X](https://x.fr/a)"
    assert judge(reply, 1, 1)[0] == "ok"


def test_judge_liens_non_verifies_doute():
    reply = "Actus : [X](https://x.fr/a)\n\nSources : [X](https://x.fr/a)"
    assert judge(reply, 0, 1)[0] == "doute"


def test_run_web_test_refus_ko_et_session_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        providers, "chat_complete",
        lambda *a, **k: (seen.update(k), "Je ne peux pas naviguer.")[1],
    )
    status, _ = run_web_test(_go(), "m")
    assert status == "ko"
    assert seen["session_id"] == f"webtest-{_go().pk}-m"
    assert seen["options"]["web"] is True


def test_run_web_test_ok_liens_verifies(monkeypatch):
    import chat.webtest as wt

    monkeypatch.setattr(
        providers, "chat_complete",
        lambda *a, **k: "Actus : [X](https://x.fr/a)\n\nSources : [X](https://x.fr/a)",
    )
    monkeypatch.setattr(wt, "verify_links", lambda urls: (2, 2))
    assert run_web_test(_go(), "m")[0] == "ok"


def test_run_web_test_erreur_provider_ko(monkeypatch):
    def _boom(*a, **k):
        raise providers.ProviderError("quota épuisé")

    monkeypatch.setattr(providers, "chat_complete", _boom)
    status, detail = run_web_test(_go(), "m")
    assert status == "ko" and "quota" in detail


def test_verify_links_borne_et_garde(monkeypatch):
    import httpx

    calls = []

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def head(self, url, **k):
            calls.append(url)
            return _Resp()
        get = head

    monkeypatch.setattr(httpx, "Client", _Client)
    urls = ["https://ok.fr/a", "http://localhost/x", "https://ok.fr/b"]
    verified, checked = verify_links(urls)
    # localhost refusé par la garde SSRF : ni testé ni compté.
    assert (verified, checked) == (2, 2)
    assert all("localhost" not in u for u in calls)


def test_vue_persiste_verdict_sans_toucher_cases(monkeypatch):
    import chat.webtest as wt

    go = _go()
    model = LLMModel.objects.create(provider=go, model_id="sonde",
                                    supports_web=True)
    monkeypatch.setattr(wt, "run_web_test", lambda p, m: ("ko", "refus"))
    # La vue importe run_web_test depuis chat.webtest à l'appel : le patch s'applique.
    resp = Client().post(f"/providers/{go.pk}/models/test-web/", {"model_id": "sonde"})
    assert resp.status_code == 302
    model.refresh_from_db()
    assert model.web_test_status == "ko"
    assert model.web_test_at is not None
    assert model.supports_web is True  # cases jamais modifiées auto


def test_vue_suggere_cocher_si_contradiction(monkeypatch):
    import chat.webtest as wt

    go = _go()
    LLMModel.objects.create(provider=go, model_id="sonde2", supports_web=False)
    monkeypatch.setattr(wt, "run_web_test", lambda p, m: ("ok", "1/1"))
    client = Client()
    resp = client.post(f"/providers/{go.pk}/models/test-web/", {"model_id": "sonde2"})
    assert resp.status_code == 302
    messages = list(resp.wsgi_request._messages)
    assert any("cocher" in str(m) for m in messages)


def test_picker_badge_teste():
    from chat.providers import model_controls

    go = _go()
    model = LLMModel.objects.create(provider=go, model_id="sonde3",
                                    web_test_status="ok")
    assert model_controls(model, str(go.base_url))["web_tested"] is True
    model.web_test_status = "ko"
    assert model_controls(model, str(go.base_url))["web_tested"] is False
