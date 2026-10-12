import json
import re

import httpx
import pytest
from django.test import Client, TestCase

from chat import providers
from chat.models import Conversation, LLMModel, Provider


def _provider(**kw):
    defaults = {"name": "LM Studio", "base_url": "http://localhost:1234/v1", "api_key": ""}
    defaults.update(kw)
    return Provider.objects.create(**defaults)


class ModelTests(TestCase):
    def test_ordering_and_cascade(self):
        p = _provider()
        Conversation.objects.create(title="B", provider=p, model_id="m")
        Conversation.objects.create(title="A", provider=p, model_id="m")
        self.assertEqual(Conversation.objects.count(), 2)
        p.delete()
        self.assertEqual(LLMModel.objects.count(), 0)


class SyncModelsTests(TestCase):
    def test_sync_ok(self, monkeypatch=None):
        pass  # couvert via pytest ci-dessous


@pytest.mark.django_db
def test_sync_models_ok(monkeypatch):
    p = _provider()
    payload = {"data": [{"id": "model-a"}, {"id": "model-b"}]}

    class FakeResp:
        def raise_for_status(self): ...
        def json(self): return payload

    class FakeClient:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    n, err = providers.sync_models(p)
    assert (n, err) == (2, "")
    assert LLMModel.objects.filter(provider=p).count() == 2


@pytest.mark.django_db
def test_sync_models_401(monkeypatch):
    p = _provider()

    class FakeClient:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            req = httpx.Request("GET", "http://x")
            resp = httpx.Response(401, request=req, text="unauthorized")
            raise httpx.HTTPStatusError("unauth", request=req, response=resp)

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    n, err = providers.sync_models(p)
    assert n == 0 and "401" in err


@pytest.mark.django_db
def test_chat_complete_ok(monkeypatch):
    p = _provider()

    class FakeResp:
        def raise_for_status(self): ...
        def json(self): return {"choices": [{"message": {"content": "Bonjour !"}}]}

    class FakeClient:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    assert providers.chat_complete(p, "m", [{"role": "user", "content": "hi"}]) == "Bonjour !"


@pytest.mark.django_db
def test_message_send_persists_error_without_provider():
    c = Conversation.objects.create(title="Nouvelle conversation")
    client = Client()
    resp = client.post(f"/conversations/{c.pk}/send/", {"content": "hello"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    c.refresh_from_db()
    assert c.messages.filter(role="user").count() == 1
    assert c.messages.filter(role="assistant").count() == 1


def test_sse_formatter():
    frames = list(providers.iter_sse_lines(["a", "b"]))
    assert frames[0].startswith("data: ")
    assert frames[-1].startswith("event: done")
    token = json.loads(frames[0][6:].strip())["token"]
    assert token == "a"


@pytest.mark.django_db
def test_settings_tabs_render():
    client = Client()
    for tab in ("general", "personnalisation", "providers", "donnees", "voix"):
        resp = client.get(f"/parametres/{tab}/")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Paramètres" in html
    resp = client.get("/parametres/inexistant/")
    assert resp.status_code == 302


@pytest.mark.django_db
def test_provider_delete_cascades_cache():
    from chat.models import LLMModel

    p = _provider()
    LLMModel.objects.create(provider=p, model_id="m1")
    client = Client()
    resp = client.post(f"/providers/{p.pk}/delete/")
    assert resp.status_code == 302
    assert not Provider.objects.filter(pk=p.pk).exists()
    assert LLMModel.objects.filter(provider_id=p.pk).count() == 0


@pytest.mark.django_db
def test_provider_update_keeps_key_when_blank():
    p = _provider(api_key="sk-secret")
    client = Client()
    client.post(f"/providers/{p.pk}/update/", {"base_url": "http://localhost:9999/v1", "api_key": ""})
    p.refresh_from_db()
    assert p.base_url == "http://localhost:9999/v1"
    assert p.api_key == "sk-secret"
    client.post(f"/providers/{p.pk}/update/", {"is_active": "0"})
    p.refresh_from_db()
    assert p.is_active is False


@pytest.mark.django_db
def test_data_clear_keeps_providers():
    _provider()
    before = Provider.objects.count()
    c = Conversation.objects.create(title="X")
    c.messages.create(role="user", content="hi")
    client = Client()
    # Sans confirm serveur : refus, rien n'est supprimé (garde anti-curl).
    resp = client.post("/donnees/effacer/")
    assert resp.status_code == 302
    assert Conversation.objects.count() == 1
    # Avec confirm : suppression effective, providers conservés.
    resp = client.post("/donnees/effacer/", {"confirm": "SUPPRIMER"})
    assert resp.status_code == 302
    assert Conversation.objects.count() == 0
    assert Provider.objects.count() == before


@pytest.mark.django_db
def test_data_export_json():
    c = Conversation.objects.create(title="Y")
    c.messages.create(role="user", content="hi")
    client = Client()
    resp = client.get("/donnees/export/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    payload = json.loads(resp.content.decode())
    assert payload[0]["title"] == "Y"
    assert payload[0]["messages"][0]["content"] == "hi"


@pytest.mark.django_db
def test_seed_providers_present_and_key_preserved():
    import importlib

    from django.apps import apps as dj_apps

    names = {"OpenCode Zen", "OpenCode Go", "OpenRouter"}
    assert names <= set(Provider.objects.values_list("name", flat=True))
    zen = Provider.objects.get(name="OpenCode Zen")
    assert zen.base_url == "https://opencode.ai/zen/v1"
    assert Provider.objects.get(name="OpenRouter").base_url == "https://openrouter.ai/api/v1"

    zen.api_key = "sk-test"
    zen.save()
    seed_mod = importlib.import_module("chat.migrations.0002_seed_providers")
    seed_mod.seed_providers(dj_apps, None)
    seed_mod.seed_providers(dj_apps, None)
    zen.refresh_from_db()
    assert zen.api_key == "sk-test"  # jamais écrasée
    assert Provider.objects.filter(name__in=names).count() == 3  # idempotent


@pytest.mark.django_db
def test_model_add_manual():
    from chat.models import LLMModel

    p = Provider.objects.get(name="OpenRouter")
    client = Client()
    resp = client.post(f"/providers/{p.pk}/models/add/", {"model_id": "x-ai/grok-code-fast-1"})
    assert resp.status_code == 302
    assert LLMModel.objects.filter(provider=p, model_id="x-ai/grok-code-fast-1").exists()


@pytest.mark.django_db
def test_model_picker_updates_conversation():
    from chat.models import LLMModel

    p = Provider.objects.get(name="OpenRouter")
    LLMModel.objects.create(provider=p, model_id="qwen3-coder")
    c = Conversation.objects.create(title="T")
    client = Client()
    resp = client.post(
        f"/conversations/{c.pk}/update/",
        {"provider_id": str(p.pk), "model_id": "qwen3-coder"},
    )
    assert resp.status_code == 302
    c.refresh_from_db()
    assert c.provider_id == p.pk
    assert c.model_id == "qwen3-coder"
    html = client.get(f"/{c.pk}/").content.decode()
    assert "qwen3-coder" in html
    assert "<select" not in html


class _UnreadStream(httpx.SyncByteStream):
    """Flux non consommé comme sur un vrai socket (MockTransport bufferise, lui)."""

    def __init__(self, data: bytes):
        self._data = data

    def __iter__(self):
        yield self._data


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


def _unread_response(status: int, body: bytes) -> httpx.Response:
    req = httpx.Request("POST", "https://x.test/chat/completions")
    return httpx.Response(
        status, headers={"Content-Type": "application/json"},
        stream=_UnreadStream(body), request=req,
    )


@pytest.mark.django_db
def test_stream_500_unread_body_gives_guided_error(monkeypatch):
    """Non-régression : .text sur réponse streaming non lue ne doit plus masquer le 500."""
    body = b'{"type":"error","error":{"type":"error","message":"Internal server error"}}'
    monkeypatch.setattr(
        providers.httpx, "stream",
        lambda *a, **k: _StreamCtx(_unread_response(500, body)),
    )
    p = Provider.objects.get(name="OpenCode Zen")
    with pytest.raises(providers.ProviderError) as excinfo:
        list(providers.chat_stream(p, "claude-sonnet-4-5", [{"role": "user", "content": "hi"}]))
    msg = str(excinfo.value)
    assert excinfo.value.status_code == 500
    assert "Internal server error" in msg  # enveloppe Anthropic extraite, pas le JSON brut
    assert "without having called" not in msg  # le vrai bug : ResponseNotRead masquant
    assert "chat/completions" in msg  # hint de route


@pytest.mark.django_db
def test_stream_200_json_error_raises_instead_of_empty(monkeypatch):
    body = b'data: {"error": {"message": "route inconnue", "code": 404}}\n\ndata: [DONE]\n\n'
    monkeypatch.setattr(
        providers.httpx, "stream",
        lambda *a, **k: _StreamCtx(_unread_response(200, body)),
    )
    p = Provider.objects.get(name="OpenCode Zen")
    with pytest.raises(providers.ProviderError) as excinfo:
        list(providers.chat_stream(p, "x", [{"role": "user", "content": "hi"}]))
    assert "route inconnue" in str(excinfo.value)


@pytest.mark.django_db
def test_stream_success_chunks(monkeypatch):
    sse = (
        b'data: {"choices": [{"delta": {"content": "Bon"}}]}\n\n'
        b'data: {"choices": [{"delta": {"content": "jour"}}]}\n\n'
        b'data: [DONE]\n\n'
    )

    class FakeClient:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False

    captured = {}

    def fake_stream(method, url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, content=sse)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(providers.httpx, "stream", fake_stream)
    p = Provider.objects.get(name="OpenRouter")
    assert list(providers.chat_stream(p, "m", [{"role": "user", "content": "hi"}])) == ["Bon", "jour"]
    assert captured["url"].endswith("/chat/completions")


def test_extract_message_envelopes():
    assert providers._extract_message('{"error": {"message": "boom", "code": 500}}') == "boom"
    assert providers._extract_message(
        '{"type":"error","error":{"type":"error","message":"oops"}}'
    ) == "oops"
    assert providers._extract_message('{"type":"error","message":"geo blocked"}') == "geo blocked"
    assert providers._extract_message('{"type":"ok","message":"hello"}') is None
    assert providers._extract_message('{"detail": "gone"}') == "gone"
    assert providers._extract_message(
        '{"detail": [{"msg": "a"}, {"msg": "b"}]}'
    ) == "{'msg': 'a'}; {'msg': 'b'}"
    assert providers._extract_message(
        '{"response": {"error": {"message": "nested"}}}'
    ) == "nested"
    assert providers._extract_message('{"choices": []}') is None
    assert providers._extract_message("pas du json") is None


@pytest.mark.django_db
def test_post_json_flat_error_reveals_message(monkeypatch, caplog):
    import logging

    _CapturingClient.payload = {"type": "error", "message": "geo blocked"}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    p = Provider.objects.get(name="OpenCode Go")
    with caplog.at_level(logging.WARNING, logger="chat.providers"):
        with pytest.raises(providers.ProviderError) as excinfo:
            providers.chat_complete(p, "muse-spark-1.3-contributor", [], route="responses")
    assert "geo blocked" in str(excinfo.value)
    assert "Erreur provider. —" not in str(excinfo.value)
    assert any("geo blocked" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_responses_completed_error_null_with_reasoning_first(monkeypatch):
    """Cas réel muse-spark : completed + error:null + reasoning avant le message."""
    _CapturingClient.payload = {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "model": "muse-spark-1.3-contributor",
        "error": None,
        "output": [
            {"id": "rs_1", "type": "reasoning", "status": "completed",
             "encrypted_content": "abc"},
            {"id": "rs_2", "type": "message", "status": "completed",
             "content": [{"type": "output_text", "text": "pip install django"}]},
        ],
    }
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    p = Provider.objects.get(name="OpenCode Go")
    assert providers.chat_complete(p, "muse-spark-1.3-contributor", [], route="responses") == (
        "pip install django"
    )


@pytest.mark.django_db
def test_responses_stream_completed_error_null_no_raise(monkeypatch):
    sse = (
        b"event: response.output_text.delta\n"
        b'data: {"type": "response.output_text.delta", "delta": "pip"}\n\n'
        b"event: response.completed\n"
        b'data: {"type": "response.completed", "response": {"status": "completed", "error": null}}\n\n'
    )

    def fake_stream(method, url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(providers.httpx, "stream", fake_stream)
    p = Provider.objects.get(name="OpenCode Go")
    assert list(providers.chat_stream(p, "muse-spark-1.3-contributor", [], route="responses")) == ["pip"]


def test_check_envelope_true_error_still_raises():
    with pytest.raises(providers.ProviderError) as excinfo:
        providers._check_envelope({"error": {"message": "boom"}}, '{"error": {"message": "boom"}}')
    assert "boom" in str(excinfo.value)
    # error: null / absent : silence
    assert providers._check_envelope({"error": None}, '{"error": null}') is None
    assert providers._check_envelope({"output": []}, '{"output": []}') is None


def test_safe_body_never_raises():
    req = httpx.Request("POST", "https://x.test/")
    resp = httpx.Response(
        500, headers={"Content-Type": "text/plain"},
        stream=_UnreadStream(b"xyz"), request=req,
    )
    assert isinstance(providers._safe_body(resp), str)


def test_codeblock_wrapper_with_lang():
    from chat.md import render_markdown

    html = render_markdown("```python\nprint('hi')\n```")
    assert 'class="codeblock"' in html
    assert ">python<" in html
    assert "copyCode(this)" in html
    assert 'class="language-python"' in html  # highlight.js côté client


def test_codeblock_wrapper_no_lang_and_xss():
    from chat.md import render_markdown

    html = render_markdown("```\nplain\n```")
    assert ">code<" in html
    # le contenu reste échappé, pas d'injection via le code
    html2 = render_markdown("```html\n<script>alert(1)</script>\n```")
    assert "<script>alert" not in html2
    assert 'class="codeblock"' in html2


def test_codeblock_markdown_lang_and_light_css():
    import pathlib

    from chat.md import render_markdown

    # Cas de la capture : bloc ```markdown avec front-matter.
    html = render_markdown("```markdown\n---\nname: js-optimizer\n---\n```")
    assert 'class="codeblock"' in html
    assert 'class="language-markdown"' in html
    assert ">markdown<" in html
    # Thème clair ChatGPT : fond gris clair, barre claire, highlight.js light.
    # Le CSS/JS highlight vit dans detail.html (extra_head), plus dans base.
    css = pathlib.Path("static/css/jejegpt.css").read_text(encoding="utf-8")
    assert ".codeblock-bar" in css
    assert "#141412" not in css
    detail_head = pathlib.Path("templates/chat/detail.html").read_text(encoding="utf-8")
    assert "github.min.css" in detail_head
    assert "github-dark" not in detail_head


def test_rich_render_tables_headings_hr():
    from chat.md import render_markdown

    text = (
        "## 🚀 2. Choisir les bons outils\n\n"
        "Vibe peut **automatiser des workflows** en combinant :\n\n"
        "- **Connecteurs** (Gmail, Slack) pour interagir.\n"
        "- **Skills** pour des tâches complexes.\n\n"
        "1. Active le connecteur **Gmail** :\n"
        "   - Autorise l'accès au compte.\n"
        "   - Vérifie la connexion.\n\n"
        "| Tâche | Comment faire |\n"
        "| --- | --- |\n"
        "| **Classer tes mails** | Utilise **Gmail** + un script. |\n"
        "| Traduire un doc | Charge via **Drive**. |\n\n"
        "---\n\n"
        "### Exemple 1 : tri Gmail\n"
    )
    html = render_markdown(text)
    assert "<h2 id=" in html and "🚀" in html
    assert "<h3 id=" in html
    assert "<strong>automatiser des workflows</strong>" in html
    assert "<ol>" in html and "<ul>" in html
    assert 'class="table-wrap"' in html and 'class="table-scroll"><table>' in html
    assert "<th>" in html and "<td>" in html
    assert "<hr" in html


def test_table_xss_still_blocked():
    from chat.md import render_markdown

    html = render_markdown("| a |\n| --- |\n| <script>alert(1)</script> |")
    assert "<script>" not in html
    assert 'class="table-wrap"' in html


def test_headers_user_agent_and_session():
    p = Provider(name="Go", base_url="https://opencode.ai/zen/go/v1", api_key="k")
    h = providers._headers(p, session_id=42)
    assert h["User-Agent"] == "JejeGPT/1.0"
    assert h["x-opencode-session"] == "42"
    assert h["Authorization"] == "Bearer k"
    h2 = providers._headers(p)
    assert "x-opencode-session" not in h2
    assert h2["User-Agent"] == "JejeGPT/1.0"


@pytest.mark.django_db
def test_chat_complete_sends_session_header(monkeypatch):
    seen = {}

    class FakeResp:
        def raise_for_status(self): ...
        def json(self): return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            seen.update(headers or {})
            return FakeResp()

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    p = Provider.objects.get(name="OpenCode Go")
    assert providers.chat_complete(p, "m", [], session_id=7) == "ok"
    assert seen["x-opencode-session"] == "7"
    assert seen["User-Agent"] == "JejeGPT/1.0"


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x.test/chat/completions")
    resp = httpx.Response(code, content=b'{"error": {"message": "boom"}}', request=req)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("devrait lever")


def test_429_zen_mentions_console_and_free_models():
    p = Provider(name="Go", base_url="https://opencode.ai/zen/go/v1")
    err = providers._friendly_error(_status_error(429), p)
    assert err.status_code == 429
    assert "opencode.ai/auth" in str(err)
    assert "union-alpha" in str(err)
    assert "Use balance" in str(err)


def test_429_generic_host_unchanged():
    p = Provider(name="Local", base_url="http://localhost:1234/v1")
    err = providers._friendly_error(_status_error(429), p)
    assert "opencode.ai/auth" not in str(err)
    assert "Quota" in str(err)


class _CapturingClient:
    """Fake httpx.Client qui capture la requête et rejoue une réponse JSON."""

    seen: dict = {}
    payload: dict = {"ok": True}

    def __init__(self, *a, **k): ...

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        type(self).seen = {"url": url, "headers": headers or {}, "json": json or {}}

        class R:
            def raise_for_status(self): ...

            def json(self_inner):
                return type(self).payload

        return R()


@pytest.mark.django_db
def test_responses_complete_items(monkeypatch):
    _CapturingClient.payload = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Salut"}]},
        ]
    }
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    p = Provider.objects.get(name="OpenCode Go")
    out = providers.chat_complete(p, "muse-spark-1.3-contributor", [], route="responses")
    assert out == "Salut"
    assert _CapturingClient.seen["url"].endswith("/responses")
    assert "input" in _CapturingClient.seen["json"]
    assert _CapturingClient.seen["headers"]["User-Agent"] == "JejeGPT/1.0"


@pytest.mark.django_db
def test_responses_complete_output_text_field(monkeypatch):
    _CapturingClient.payload = {"output_text": "Yo"}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    p = Provider.objects.get(name="OpenCode Go")
    assert providers.chat_complete(p, "m", [], route="responses") == "Yo"


@pytest.mark.django_db
def test_responses_stream_deltas_and_refusal(monkeypatch):
    sse = (
        b"event: response.output_text.delta\n"
        b'data: {"type": "response.output_text.delta", "delta": "Bon"}\n\n'
        b"event: response.output_text.delta\n"
        b'data: {"type": "response.output_text.delta", "delta": "jour"}\n\n'
        b"event: response.refusal.delta\n"
        b'data: {"type": "response.refusal.delta", "delta": "non"}\n\n'
        b"event: response.completed\n"
        b'data: {"type": "response.completed"}\n\n'
    )

    def fake_stream(method, url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(providers.httpx, "stream", fake_stream)
    p = Provider.objects.get(name="OpenCode Go")
    assert list(providers.chat_stream(p, "m", [], route="responses")) == ["Bon", "jour", "Refus : non"]


@pytest.mark.django_db
def test_responses_stream_error_event(monkeypatch):
    body = b'event: error\ndata: {"type": "error", "message": "bad route"}\n\n'
    monkeypatch.setattr(
        providers.httpx, "stream",
        lambda *a, **k: _StreamCtx(_unread_response(200, body)),
    )
    p = Provider.objects.get(name="OpenCode Go")
    with pytest.raises(providers.ProviderError) as excinfo:
        list(providers.chat_stream(p, "m", [], route="responses"))
    assert "bad route" in str(excinfo.value)


@pytest.mark.django_db
def test_messages_complete_system_separate_and_headers(monkeypatch):
    _CapturingClient.payload = {"content": [{"type": "text", "text": "Hello"}]}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    p = Provider.objects.get(name="OpenCode Go")
    out = providers.chat_complete(
        p, "qwen3.7-plus",
        [{"role": "system", "content": "Sois bref."}, {"role": "user", "content": "hi"}],
        route="messages",
    )
    assert out == "Hello"
    seen = _CapturingClient.seen
    assert seen["url"].endswith("/messages")
    assert seen["json"]["system"] == "Sois bref."
    assert seen["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["json"]["max_tokens"] == providers.MESSAGES_MAX_TOKENS
    assert seen["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.django_db
def test_messages_stream_deltas(monkeypatch):
    sse = (
        b"event: content_block_delta\n"
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Sa"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lut"}}\n\n'
        b"event: message_stop\n"
        b'data: {"type": "message_stop"}\n\n'
    )

    def fake_stream(method, url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(providers.httpx, "stream", fake_stream)
    p = Provider.objects.get(name="OpenCode Go")
    assert list(providers.chat_stream(p, "qwen3.7-plus", [], route="messages")) == ["Sa", "lut"]


@pytest.mark.django_db
def test_messages_stream_500_unread_body(monkeypatch):
    body = b'{"type":"error","error":{"type":"error","message":"Internal server error"}}'
    monkeypatch.setattr(
        providers.httpx, "stream",
        lambda *a, **k: _StreamCtx(_unread_response(500, body)),
    )
    p = Provider.objects.get(name="OpenCode Go")
    with pytest.raises(providers.ProviderError) as excinfo:
        list(providers.chat_stream(p, "qwen3.7-plus", [], route="messages"))
    assert excinfo.value.status_code == 500
    assert "Internal server error" in str(excinfo.value)


@pytest.mark.django_db
def test_resolve_route_cache_then_table_then_default():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="qwen3.7-plus", route="messages")
    assert providers.resolve_route(go, "qwen3.7-plus") == "messages"
    assert providers.resolve_route(go, "glm-5.3-flash") == ""
    assert providers.resolve_route(go, "muse-spark-1.3-contributor") == "responses"
    local = Provider(name="L", base_url="http://localhost:1234/v1")
    assert providers.resolve_route(local, "qwen3.7-plus") == ""
    assert providers.resolve_route(None, "x") == ""


def test_lookup_route():
    go = "https://opencode.ai/zen/go/v1"
    assert providers.lookup_route(go, "muse-spark-1.3-contributor") == "responses"
    assert providers.lookup_route(go, "qwen3.7-plus") == "messages"
    assert providers.lookup_route(go, "glm-5.3-flash") == ""
    assert providers.lookup_route("https://opencode.ai/zen/v1", "gpt-5.6-luna") == "responses"
    assert providers.lookup_route("https://opencode.ai/zen/v1", "kimi-k2") == ""
    assert providers.lookup_route("https://openrouter.ai/api/v1", "qwen3.7-plus") == ""
    assert providers.lookup_route("http://localhost:1234/v1", "nimporte") == ""


@pytest.mark.django_db
def test_sync_fills_route(monkeypatch):
    monkeypatch.setattr(
        providers, "fetch_models", lambda p, timeout=30.0: ["glm-5.3-flash", "qwen3.7-plus"]
    )
    p = Provider.objects.get(name="OpenCode Go")
    n, err = providers.sync_models(p)
    assert (n, err) == (2, "")
    routes = dict(p.cached_models.values_list("model_id", "route"))
    assert routes == {"glm-5.3-flash": "", "qwen3.7-plus": "messages"}


@pytest.mark.django_db
def test_picker_badges_non_chat_models():
    from chat.models import LLMModel

    p = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=p, model_id="muse-spark-1.3-contributor", route="responses")
    c = Conversation.objects.create(title="T", provider=p, model_id="glm-5.3-flash")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "/responses" in html
    assert "route /responses" in html  # chip informative de route supportée


@pytest.mark.django_db
def test_send_dispatches_responses_route(monkeypatch):
    _CapturingClient.payload = {"output_text": "Salut spark"}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="T", provider=go, model_id="muse-spark-1.3-contributor")
    resp = Client().post(f"/conversations/{c.pk}/send/", {"content": "hi"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert _CapturingClient.seen["url"].endswith("/responses")
    assert c.messages.filter(role="assistant", content="Salut spark").exists()


@pytest.mark.django_db
def test_effective_caps_cache_table_default():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    assert providers.effective_caps(go, "glm-5.3-flash") == {"effort": False, "web": False, "thinking": False}
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash", supports_web=True)
    assert providers.effective_caps(go, "glm-5.3-flash")["web"] is True  # cache prime
    assert providers.effective_caps(go, "muse-spark-1.3-contributor")["effort"] is True  # table
    assert providers.effective_caps(None, "x") == {"effort": False, "web": False, "thinking": False}


@pytest.mark.django_db
def test_sync_does_not_overwrite_manual_caps(monkeypatch):
    from chat.models import LLMModel

    monkeypatch.setattr(providers, "fetch_models", lambda p, timeout=30.0: ["glm-5.3-flash"])
    p = Provider.objects.get(name="OpenCode Go")
    providers.sync_models(p)
    m = LLMModel.objects.get(provider=p, model_id="glm-5.3-flash")
    assert (m.supports_effort, m.supports_web, m.supports_thinking) == (False, False, False)
    client = Client()
    client.post(f"/providers/{p.pk}/models/caps/", {
        "model_id": "glm-5.3-flash", "supports_effort": "1", "supports_web": "1",
    })
    providers.sync_models(p)  # re-sync : l'override manuel survit
    m.refresh_from_db()
    assert (m.supports_effort, m.supports_web, m.supports_thinking) == (True, True, False)


@pytest.mark.django_db
def test_ui_controls_gating():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(
        provider=go, model_id="muse-spark-1.3-contributor", route="responses",
        supports_effort=True, supports_web=True, supports_thinking=True,
    )
    LLMModel.objects.create(provider=go, model_id="qwen3.7-plus", route="messages", supports_thinking=True)
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash")
    assert providers.ui_controls(go, "muse-spark-1.3-contributor") == {
        "route": "responses", "effort": True, "web": True, "thinking": False, "known": True,
        "web_tested": False}
    assert providers.ui_controls(go, "qwen3.7-plus") == {
        "route": "messages", "effort": True, "web": False, "thinking": True, "known": True,
        "web_tested": False}
    assert providers.ui_controls(go, "glm-5.3-flash") == {
        "route": "", "effort": False, "web": False, "thinking": False, "known": True,
        "web_tested": False}
    assert providers.ui_controls(go, "omen-alpha")["known"] is False  # inconnu -> non vérifié
    # ligne cache vide (sync sans métadonnées) -> non vérifié, pas non supporté
    LLMModel.objects.create(provider=go, model_id="modele-mystere")
    assert providers.ui_controls(go, "modele-mystere")["known"] is False
    # sans ligne cache : la table statique prime (web True pour muse-spark)
    LLMModel.objects.filter(provider=go, model_id="muse-spark-1.3-contributor").delete()
    assert providers.ui_controls(go, "muse-spark-1.3-contributor")["web"] is True


@pytest.mark.django_db
def test_responses_mapping_effort_web(monkeypatch):
    from chat.models import LLMModel

    _CapturingClient.payload = {"output_text": "ok"}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="muse-spark-1.3-contributor",
                            route="responses", supports_effort=True, supports_web=True)
    out = providers.chat_complete(
        go, "muse-spark-1.3-contributor", [], route="responses",
        options={"effort": "high", "web": True, "thinking": True},
    )
    assert out == "ok"
    body = _CapturingClient.seen["json"]
    assert body["reasoning"] == {"effort": "high"}
    assert body["tools"] == [{"type": "web_search"}]


@pytest.mark.django_db
def test_responses_mapping_gated_when_unsupported(monkeypatch):
    _CapturingClient.payload = {"output_text": "ok"}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    providers.chat_complete(
        go, "glm-5.3-flash", [], route="responses",
        options={"effort": "high", "web": True, "thinking": False},
    )
    body = _CapturingClient.seen["json"]
    assert "reasoning" not in body
    assert "tools" not in body


@pytest.mark.django_db
def test_messages_mapping_thinking_budget_and_temp(monkeypatch):
    from chat.models import LLMModel

    _CapturingClient.payload = {"content": [{"type": "text", "text": "hi"}]}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="qwen3.7-plus",
                            route="messages", supports_thinking=True)
    out = providers.chat_complete(
        go, "qwen3.7-plus", [{"role": "user", "content": "hi"}],
        temperature=0.3, route="messages",
        options={"effort": "high", "web": False, "thinking": True},
    )
    assert out == "hi"
    body = _CapturingClient.seen["json"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert body["temperature"] == 1.0  # thinking exige temperature=1
    assert "tools" not in body


@pytest.mark.django_db
def test_messages_mapping_thinking_disabled(monkeypatch):
    from chat.models import LLMModel

    _CapturingClient.payload = {"content": [{"type": "text", "text": "hi"}]}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="qwen3.7-plus",
                            route="messages", supports_thinking=True)
    providers.chat_complete(
        go, "qwen3.7-plus", [], temperature=0.3, route="messages",
        options={"effort": "", "web": False, "thinking": False},
    )
    body = _CapturingClient.seen["json"]
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.3  # pas de thinking -> température gardée


@pytest.mark.django_db
def test_conversation_update_options():
    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="T", provider=go, model_id="m")
    client = Client()
    client.post(f"/conversations/{c.pk}/update/", {
        "reasoning_effort": "high", "web_search": "1", "thinking_enabled": "0"})
    c.refresh_from_db()
    assert (c.reasoning_effort, c.web_search, c.thinking_enabled) == ("high", True, False)
    client.post(f"/conversations/{c.pk}/update/", {"reasoning_effort": "ultra"})
    c.refresh_from_db()
    assert c.reasoning_effort == ""  # valeur inconnue -> Auto


@pytest.mark.django_db
def test_composer_shows_only_supported_controls():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="muse-spark-1.3-contributor",
                            route="responses", supports_effort=True)
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash")
    c = Conversation.objects.create(title="T", provider=go, model_id="muse-spark-1.3-contributor")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "Effort de raisonnement" in html
    assert '{"web_search"' not in html  # web non supporté : pastille grisée, pas de toggle
    assert "non supportée par" in html or "non vérifiées" in html
    c.model_id = "glm-5.3-flash"
    c.save()
    html2 = Client().get(f"/{c.pk}/").content.decode()
    # Assertion scopée au toggle du composer (« , niveau ») : le picker liste aussi
    # les badges des autres modèles (dont l'Effort de muse-spark), ce qui est voulu.
    assert "Effort de raisonnement, niveau" not in html2


@pytest.mark.django_db
def test_composer_states_and_scroll():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="muse-spark-1.3-contributor",
                            route="responses", supports_effort=True, supports_web=True)
    c = Conversation.objects.create(title="T", provider=go, model_id="muse-spark-1.3-contributor")
    c.messages.create(role="user", content="hi")
    html = Client().get(f"/{c.pk}/").content.decode()
    # web actif : toggle présent
    assert '{"web_search": "1"}' in html or '{"web_search":"1"}' in html.replace(" ", "")
    # thinking responses : pastille explicative, pas de toggle
    assert "ne se coupe pas via l'API Responses" in html
    assert 'name="thinking_enabled"' not in html
    # scroll sticky : suivi conditionnel, pastille, ancre relative au conteneur
    assert "followOutput" in html
    assert "Dernière réponse" in html
    assert "getBoundingClientRect" in html
    # modèle inconnu : états non vérifiés
    c.model_id = "omen-alpha"
    c.save()
    html3 = Client().get(f"/{c.pk}/").content.decode()
    assert "non vérifiées" in html3


@pytest.mark.django_db
def test_effort_label_persistent():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="muse-spark-1.3-contributor",
                            route="responses", supports_effort=True)
    c = Conversation.objects.create(title="T", provider=go, model_id="muse-spark-1.3-contributor")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert ">auto</span>" in html  # défaut : pastille auto
    c.reasoning_effort = "high"
    c.save()
    html2 = Client().get(f"/{c.pk}/").content.decode()
    assert ">high</span>" in html2  # niveau fixé visible en toutes lettres
    assert "niveau high" in html2  # aria-label enrichi


def test_base_uses_browser_highlight_build():
    import pathlib

    # highlight.js vit dans detail.html (extra_head) : global = base, cible = detail.
    base = pathlib.Path("templates/base.html").read_text(encoding="utf-8")
    detail = pathlib.Path("templates/chat/detail.html").read_text(encoding="utf-8")
    assert "cdnjs.cloudflare.com/ajax/libs/highlight.js" in detail
    assert "cdnjs.cloudflare.com/ajax/libs/highlight.js" not in base
    assert "lib/common.min.js" not in detail
    # Thème clair : highlight.js light obligatoire, dark interdit.
    assert "styles/github.min.css" in detail
    assert "github-dark" not in detail


@pytest.mark.django_db
def test_hero_composer_single_row_no_static_reflexion():
    c = Conversation.objects.create(title="T")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "Réflexion ▾" not in html  # doublon décoratif supprimé (toggle ampoule à la place)
    assert '<textarea name="content"' in html
    assert 'class="w-full min-w-0 px-1 py-2"' in html  # textarea pleine largeur
    assert 'class="composer-bar flex items-center gap-1"' in html  # outils sous le texte
    assert html.count("shrink-0") >= 2  # boutons fixes insécables


@pytest.mark.django_db
def test_thread_partial_endpoint():
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="hello")
    resp = Client().get(f"/conversations/{c.pk}/thread/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "hello" in html
    assert "<html" not in html  # partiel seul, pas de page complète
    assert Client().get("/conversations/9999/thread/").status_code == 404


@pytest.mark.django_db
def test_detail_uses_live_stream_composer():
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="hi")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "sendStream($el, false)" in html
    assert f"/conversations/{c.pk}/thread/" in html  # URL résolue du partiel post-stream
    assert "stream-shimmer" in html  # shimmer moderne pendant la génération
    assert "marked.parse" in html and "DOMPurify" in html  # markdown rendu en direct
    assert "liveText" not in html  # ancienne zone supprimée
    assert "Stream temps réel" not in html  # ancien bouton séparé supprimé
    assert "thread-live" in html  # conteneur live centré (pas de pleine largeur)
    assert "getElementById('thread-box')" in html  # thread existant prioritaire


@pytest.mark.django_db
def test_default_llm_singleton_and_inheritance():
    from chat.models import AppSettings, LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash")
    assert AppSettings.get_solo().pk == AppSettings.get_solo().pk == 1
    assert AppSettings.get_solo().default_valid() is False  # rien configuré
    defaults = AppSettings.get_solo()
    defaults.default_provider = go
    defaults.default_model_id = "glm-5.3-flash"
    defaults.save()
    assert AppSettings.get_solo().default_valid() is True
    client = Client()
    client.post("/conversations/new/")
    conv = Conversation.objects.latest("id")
    assert conv.provider_id == go.pk
    assert conv.model_id == "glm-5.3-flash"


@pytest.mark.django_db
def test_default_llm_invalid_falls_back():
    from chat.models import AppSettings

    defaults = AppSettings.get_solo()
    go = Provider.objects.get(name="OpenCode Go")
    defaults.default_provider = go
    defaults.default_model_id = "modele-inexistant"
    defaults.save()
    assert defaults.default_valid() is False
    Client().post("/conversations/new/")
    conv = Conversation.objects.latest("id")
    assert conv.provider is not None  # repli premier actif, sans crash


@pytest.mark.django_db
def test_settings_save_validates():
    from chat.models import AppSettings, LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash")
    client = Client()
    resp = client.post("/parametres-enregistrer/", {"default_choice": f"{go.pk}:glm-5.3-flash"})
    assert resp.status_code == 302
    defaults = AppSettings.get_solo()
    assert (defaults.default_provider_id, defaults.default_model_id) == (go.pk, "glm-5.3-flash")
    # invalide : provider inexistant -> inchangé
    client.post("/parametres-enregistrer/", {"default_choice": "9999:glm-5.3-flash"})
    defaults.refresh_from_db()
    assert (defaults.default_provider_id, defaults.default_model_id) == (go.pk, "glm-5.3-flash")
    # invalide : modèle hors cache -> inchangé
    client.post("/parametres-enregistrer/", {"default_choice": f"{go.pk}:nope"})
    defaults.refresh_from_db()
    assert defaults.default_model_id == "glm-5.3-flash"
    # malformé / absent -> inchangé, sans crash
    client.post("/parametres-enregistrer/", {"default_choice": "nimporte-quoi"})
    client.post("/parametres-enregistrer/", {})
    defaults.refresh_from_db()
    assert defaults.default_model_id == "glm-5.3-flash"


@pytest.mark.django_db
def test_general_tab_lists_models_grouped():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="glm-5.3-flash")
    LLMModel.objects.create(provider=go, model_id="qwen3.7-plus", route="messages")
    html = Client().get("/parametres/general/").content.decode()
    assert "<optgroup" in html and "OpenCode Go" in html
    assert "glm-5.3-flash" in html
    assert "qwen3.7-plus · /messages" in html
    assert 'name="model_id"' not in html  # fini la saisie libre


@pytest.mark.django_db
def test_create_inherits_default_system_prompt():
    from django.test import override_settings

    with override_settings(JEJEGPT_DEFAULT_SYSTEM_PROMPT="STYLE-TEST"):
        Client().post("/conversations/new/")
        conv = Conversation.objects.latest("id")
        assert conv.system_prompt == "STYLE-TEST"


@pytest.mark.django_db
def test_create_empty_default_system_prompt():
    from django.test import override_settings

    with override_settings(JEJEGPT_DEFAULT_SYSTEM_PROMPT=""):
        Client().post("/conversations/new/")
        conv = Conversation.objects.latest("id")
        assert conv.system_prompt == ""


def test_default_system_prompt_content():
    from django.conf import settings as dj_settings

    prompt = dj_settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT
    assert "##" in prompt and "tableau" in prompt.lower() and "Markdown" in prompt


@pytest.mark.django_db
def test_perso_preset_button_present():
    Conversation.objects.create(title="T")
    html = Client().get("/parametres/personnalisation/").content.decode()
    assert "Style structuré" in html
    assert "data-preset" in html


@pytest.mark.django_db
def test_message_edit_updates_drops_following_and_regenerates(monkeypatch):
    _CapturingClient.payload = {"choices": [{"message": {"content": "nouvelle réponse"}}]}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="T", provider=go, model_id="m")
    q1 = c.messages.create(role="user", content="question 1")
    c.messages.create(role="assistant", content="réponse 1")
    c.messages.create(role="user", content="question 2")
    c.messages.create(role="assistant", content="réponse 2")
    resp = Client().post(f"/messages/{q1.pk}/edit/", {"content": "question 1 bis"})
    assert resp.status_code == 200
    q1.refresh_from_db()
    assert q1.content == "question 1 bis"
    remaining = list(c.messages.order_by("created_at", "id").values_list("role", "content"))
    assert remaining == [("user", "question 1 bis"), ("assistant", "nouvelle réponse")]
    assert "nouvelle réponse" in resp.content.decode()


@pytest.mark.django_db
def test_message_edit_guards():
    c = Conversation.objects.create(title="T")
    a = c.messages.create(role="assistant", content="r")
    assert Client().post(f"/messages/{a.pk}/edit/", {"content": "x"}).status_code == 400
    assert Client().post("/messages/9999/edit/", {"content": "x"}).status_code == 404
    q = c.messages.create(role="user", content="q")
    assert Client().post(f"/messages/{q.pk}/edit/", {"content": "  "}).status_code == 400
    q.refresh_from_db()
    assert q.content == "q"  # vide -> rien modifié


@pytest.mark.django_db
def test_message_regenerate_from_drops_following(monkeypatch):
    _CapturingClient.payload = {"choices": [{"message": {"content": "regen"}}]}
    monkeypatch.setattr(providers.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="T", provider=go, model_id="m")
    q1 = c.messages.create(role="user", content="q1")
    c.messages.create(role="assistant", content="vieille réponse")
    resp = Client().post(f"/messages/{q1.pk}/regenerate/")
    assert resp.status_code == 200
    remaining = list(c.messages.order_by("created_at", "id").values_list("content", flat=True))
    assert remaining == ["q1", "regen"]
    assert Client().post("/messages/9999/regenerate/").status_code == 404


@pytest.mark.django_db
def test_thread_action_bar_rendered():
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="ma question")
    c.messages.create(role="assistant", content="réponse")
    html = Client().get(f"/{c.pk}/").content.decode()
    # question : copier, modifier, régénérer ; réponse : copier, MD, écouter,
    # +/-, régénérer, pin, fork, canvas, retry-with, 3 canaux newsletter
    assert html.count("msg-action") == 16
    assert 'data-tip="Copier"' in html
    assert 'data-tip="Modifier"' in html
    assert "Régénérer la réponse" in html
    assert "Bonne réponse" in html
    assert "Mauvaise réponse" in html
    assert "copyMsg(this)" in html
    assert "Enregistrer et régénérer" in html
    assert "Copier en Markdown" in html
    assert "Ouvrir en Canvas" in html
    assert "comparo A-B" in html
    assert "selection-toolbar" in html
    assert "followups" in html or "Approfondis" in html


def _max_form_depth(html: str) -> int:
    from html.parser import HTMLParser

    depths = [0]

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "form":
                depths[0] += 1
                depths.append(depths[0])

        def handle_endtag(self, tag):
            if tag == "form":
                depths[0] -= 1

    P().feed(html)
    return max(depths)


@pytest.mark.django_db
def test_no_nested_forms_on_chat_pages():
    """Anti-récidive : aucun <form> dans un <form> (le parseur fermerait le parent)."""
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="q")
    assert _max_form_depth(Client().get(f"/{c.pk}/").content.decode()) == 1
    c.messages.create(role="assistant", content="r")
    assert _max_form_depth(Client().get(f"/{c.pk}/").content.decode()) == 1
    assert _max_form_depth(Client().get("/parametres/providers/").content.decode()) == 1


@pytest.mark.django_db
def test_composer_options_postopt_buttons():
    from chat.models import LLMModel

    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="muse-spark-1.3-contributor",
                            route="responses", supports_effort=True)
    c = Conversation.objects.create(title="T", provider=go, model_id="muse-spark-1.3-contributor")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "postOpt($el)" in html
    assert "data-params" in html
    # params JSON valides, effort parmi les niveaux autorisés
    params = [json.loads(v) for v in re.findall(r"data-params='(\{.*?\})'", html)]
    assert params, "aucun data-params trouvé"
    for p in params:
        if "reasoning_effort" in p:
            assert p["reasoning_effort"] in ("", "low", "medium", "high", "xhigh")


def test_usage_helpers():
    from chat import providers as _pv

    assert _pv._empty_usage() == {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "duration_ms": 0}
    col = _pv._empty_usage()
    _pv._fill_usage(col, 10, 5)
    assert (col["prompt_tokens"], col["completion_tokens"], col["total_tokens"]) == (10, 5, 15)
    col2 = _pv._empty_usage()
    _pv._fill_usage(col2, "x", -3, None)  # tolérant
    assert (col2["prompt_tokens"], col2["completion_tokens"], col2["total_tokens"]) == (0, 0, 0)
    assert _pv._extract_message('{"x": 1}') is None  # non régression extracteur


@pytest.mark.django_db
def test_usage_captured_chat_sync(monkeypatch):
    from chat import providers as _pv

    class FakeResp:
        def raise_for_status(self): ...

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}}

    class FakeClient:
        def __init__(self, *a, **k): ...

        def __enter__(self): return self

        def __exit__(self, *a): return False

        def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr(_pv.httpx, "Client", FakeClient)
    p = Provider.objects.get(name="OpenRouter")
    col = _pv._empty_usage()
    assert _pv.chat_complete(p, "m", [], usage_collector=col) == "hi"
    assert (col["prompt_tokens"], col["completion_tokens"], col["total_tokens"]) == (11, 4, 15)
    assert col["duration_ms"] >= 0


@pytest.mark.django_db
def test_usage_captured_chat_stream_final_chunk(monkeypatch):
    from chat import providers as _pv

    sse = (
        b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n'
        b'data: {"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}\n\n'
        b"data: [DONE]\n\n"
    )

    def fake_stream(method, url, **kwargs):
        def handler(request):
            return httpx.Response(200, content=sse)

        return httpx.Client(transport=httpx.MockTransport(handler)).stream(method, url, **kwargs)

    monkeypatch.setattr(_pv.httpx, "stream", fake_stream)
    p = Provider.objects.get(name="OpenRouter")
    col = _pv._empty_usage()
    assert list(_pv.chat_stream(p, "m", [], usage_collector=col)) == ["a"]
    assert (col["prompt_tokens"], col["completion_tokens"], col["total_tokens"]) == (7, 3, 10)


@pytest.mark.django_db
def test_usage_captured_responses_and_messages(monkeypatch):
    from chat import providers as _pv

    _CapturingClient.payload = {
        "output_text": "yo", "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}
    monkeypatch.setattr(_pv.httpx, "Client", _CapturingClient)
    go = Provider.objects.get(name="OpenCode Go")
    col = _pv._empty_usage()
    assert _pv.chat_complete(go, "m", [], route="responses", usage_collector=col) == "yo"
    assert (col["prompt_tokens"], col["completion_tokens"], col["total_tokens"]) == (5, 2, 7)

    _CapturingClient.payload = {
        "content": [{"type": "text", "text": "hey"}],
        "usage": {"input_tokens": 9, "output_tokens": 6}}
    col2 = _pv._empty_usage()
    assert _pv.chat_complete(go, "m", [], route="messages", usage_collector=col2) == "hey"
    assert (col2["prompt_tokens"], col2["completion_tokens"], col2["total_tokens"]) == (9, 6, 15)


@pytest.mark.django_db
def test_usage_captured_messages_stream(monkeypatch):
    from chat import providers as _pv

    sse = (
        b"event: message_start\n"
        b'data: {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 0}}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "z"}}\n\n'
        b"event: message_delta\n"
        b'data: {"type": "message_delta", "usage": {"output_tokens": 4}}\n\n'
        b"event: message_stop\n"
        b'data: {"type": "message_stop"}\n\n'
    )

    def fake_stream(method, url, **kwargs):
        def handler(request):
            return httpx.Response(200, content=sse)

        return httpx.Client(transport=httpx.MockTransport(handler)).stream(method, url, **kwargs)

    monkeypatch.setattr(_pv.httpx, "stream", fake_stream)
    go = Provider.objects.get(name="OpenCode Go")
    col = _pv._empty_usage()
    assert list(_pv.chat_stream(go, "m", [], route="messages", usage_collector=col)) == ["z"]
    assert (col["prompt_tokens"], col["completion_tokens"], col["total_tokens"]) == (12, 4, 16)


@pytest.mark.django_db
def test_send_persists_usage_record(monkeypatch):
    from chat import providers as _pv
    from chat.models import UsageRecord

    _CapturingClient.payload = {
        "choices": [{"message": {"content": "pong"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}}
    monkeypatch.setattr(_pv.httpx, "Client", _CapturingClient)
    local = Provider.objects.create(name="T", base_url="http://localhost:9/v1")
    c = Conversation.objects.create(title="T", provider=local, model_id="m")
    Client().post(f"/conversations/{c.pk}/send/", {"content": "ping"}, HTTP_HX_REQUEST="true")
    rec = UsageRecord.objects.get(conversation=c)
    assert (rec.prompt_tokens, rec.completion_tokens, rec.total_tokens) == (20, 8, 28)
    assert rec.success is True and rec.model_id == "m" and rec.duration_ms >= 0


@pytest.mark.django_db
def test_stream_view_persists_usage(monkeypatch):
    from chat import providers as _pv
    from chat.models import UsageRecord

    sse = (
        b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n'
        b'data: {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}\n\n'
        b"data: [DONE]\n\n"
    )

    def fake_stream(method, url, **kwargs):
        def handler(request):
            return httpx.Response(200, content=sse)

        return httpx.Client(transport=httpx.MockTransport(handler)).stream(method, url, **kwargs)

    monkeypatch.setattr(_pv.httpx, "stream", fake_stream)
    local = Provider.objects.create(name="T2", base_url="http://localhost:9/v1")
    c = Conversation.objects.create(title="T", provider=local, model_id="m")
    resp = Client().get(f"/conversations/{c.pk}/stream/?prompt=hello")
    assert resp.status_code == 200
    b"".join(resp.streaming_content)  # consomme le générateur
    rec = UsageRecord.objects.get(conversation=c)
    assert (rec.total_tokens, rec.success) == (5, True)


@pytest.mark.django_db
def test_utilisation_tab_aggregates_and_renders():
    import datetime

    from django.utils import timezone as _tz

    from chat.models import UsageRecord

    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="Conv", provider=go, model_id="glm-5.3-flash")
    UsageRecord.objects.create(
        conversation=c, provider=go, model_id="glm-5.3-flash", route="",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        duration_ms=2000, success=True)
    UsageRecord.objects.create(
        conversation=c, provider=go, model_id="qwen3.7-plus", route="messages",
        prompt_tokens=10, completion_tokens=5, total_tokens=15,
        duration_ms=1000, success=False, error="boom")
    # antidaté pour la heatmap (toujours dans la période 90 j)
    old = UsageRecord.objects.create(
        conversation=c, provider=go, model_id="glm-5.3-flash", route="",
        prompt_tokens=1, completion_tokens=1, total_tokens=2, duration_ms=100, success=True)
    UsageRecord.objects.filter(pk=old.pk).update(
        created_at=_tz.now() - datetime.timedelta(days=3))
    html = Client().get("/parametres/utilisation/?period=90").content.decode()
    assert "Utilisation" in html
    assert ">3<" in html  # 3 appels
    assert "glm-5.3-flash" in html and "qwen3.7-plus" in html
    assert "heat-l" in html  # heatmap rendue
    assert 'title="boom"' in html  # détail d'erreur au survol uniquement, pas en clair
    assert ">boom<" not in html
    assert "Erreur" in html
    html7 = Client().get("/parametres/utilisation/?period=7").content.decode()
    assert ">3<" in html7


@pytest.mark.django_db
def test_utilisation_empty_state():
    html = Client().get("/parametres/utilisation/").content.decode()
    assert "Aucune donnée pour l'instant" in html


@pytest.mark.django_db
def test_composer_shows_last_usage_with_tooltips():
    from chat.models import UsageRecord

    go = Provider.objects.get(name="OpenCode Go")
    c = Conversation.objects.create(title="T", provider=go, model_id="glm-5.3-flash")
    c.messages.create(role="user", content="hi")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "tok/s" not in html  # rien sans réponse
    UsageRecord.objects.create(
        conversation=c, provider=go, model_id="glm-5.3-flash", route="",
        prompt_tokens=120, completion_tokens=60, total_tokens=180,
        duration_ms=3000, success=True)
    html2 = Client().get(f"/{c.pk}/").content.decode()
    assert "120 in" in html2 and "60 out" in html2
    assert "20,0 tok/s" in html2 and "3,0 s" in html2  # format FR localisé
    assert "Tokens d'entrée" in html2 and "Vitesse" in html2 and "Durée" in html2


@pytest.mark.django_db
def test_thread_partial_no_forced_scroll():
    import pathlib

    partial = pathlib.Path("templates/chat/partials/thread.html").read_text(encoding="utf-8")
    assert "scrollHeight" not in partial  # fini le saut en bas à chaque rendu
    # Coloration conservée mais ciblée (blocs non colorés seuls, plus de scan global).
    assert "highlightElement" in partial and "highlightAll" not in partial


@pytest.mark.django_db
def test_stream_anchors_question_not_bottom():
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="hi")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "getBoundingClientRect" in html  # mesure relative au conteneur
    assert "offsetTop" not in html  # ancien calcul faux (relatif au main)
    assert "lastQuestionEl" in html  # ancre = dernière question répondue
    assert "followOutput" in html and "Dernière réponse" in html


def test_render_markdown_liens_cliquables():
    from chat.md import render_markdown

    html = render_markdown("Source : [GitHub Trending](https://github.com/trending) + https://example.com/x")
    assert 'href="https://github.com/trending"' in html
    assert 'target="_blank"' in html
    assert 'href="https://example.com/x"' in html  # URL brute auto-liée


def test_render_markdown_pas_de_lien_dans_code_ni_xss():
    from chat.md import render_markdown

    html = render_markdown("```\nhttps://example.com/x\n```")
    assert "https://example.com/x" in html
    assert "<a " not in html  # pas de lien dans les blocs de code
    html2 = render_markdown('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in html2
