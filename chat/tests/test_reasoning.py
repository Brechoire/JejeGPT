import pytest
from django.test import Client

from chat import providers
from chat.models import Conversation, Message, Provider

pytestmark = pytest.mark.django_db


def _fake_stream(sse: bytes):
    import httpx

    def fake_stream(method, url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    return fake_stream


def _provider():
    return Provider.objects.create(name="RP", base_url="http://localhost:9/v1")


# ---------------- Capture par route ----------------

def test_chat_route_reasoning_content(monkeypatch):
    sse = (
        b'data: {"choices": [{"delta": {"reasoning_content": "Je r\xc3\xa9fl\xc3\xa9chis"}}]}\n\n'
        b'data: {"choices": [{"delta": {"content": "Salut"}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    monkeypatch.setattr(providers.httpx, "stream", _fake_stream(sse))
    seen = []
    tokens = list(providers.chat_stream(
        _provider(), "m", [{"role": "user", "content": "hi"}],
        reasoning_callback=seen.append,
    ))
    assert tokens == ["Salut"]  # réponse intacte, raisonnement à part
    assert seen == ["Je réfléchis"]


def test_responses_route_reasoning_summary(monkeypatch):
    sse = (
        b'event: response.reasoning_summary_text.delta\n'
        b'data: {"type": "response.reasoning_summary_text.delta", "delta": "analyse"}\n\n'
        b'event: response.output_text.delta\n'
        b'data: {"type": "response.output_text.delta", "delta": "pip"}\n\n'
        b'event: response.completed\ndata: {"type": "response.completed", "usage": {}}\n\n'
    )
    monkeypatch.setattr(providers.httpx, "stream", _fake_stream(sse))
    seen = []
    tokens = list(providers.chat_stream(
        _provider(), "m", [], route="responses", reasoning_callback=seen.append,
    ))
    assert tokens == ["pip"]
    assert seen == ["analyse"]


def test_messages_route_thinking_delta(monkeypatch):
    sse = (
        b'data: {"type": "content_block_delta", '
        b'"delta": {"type": "thinking_delta", "thinking": "hmm"}}\n\n'
        b'data: {"type": "content_block_delta", '
        b'"delta": {"type": "text_delta", "text": "Salut"}}\n\n'
        b'data: {"type": "message_stop"}\n\n'
    )
    monkeypatch.setattr(providers.httpx, "stream", _fake_stream(sse))
    seen = []
    tokens = list(providers.chat_stream(
        _provider(), "m", [], route="messages", reasoning_callback=seen.append,
    ))
    assert tokens == ["Salut"]
    assert seen == ["hmm"]


def test_sans_callback_pas_de_crash(monkeypatch):
    sse = (
        b'data: {"choices": [{"delta": {"reasoning_content": "x", "content": "y"}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    monkeypatch.setattr(providers.httpx, "stream", _fake_stream(sse))
    assert list(providers.chat_stream(_provider(), "m", [])) == ["y"]


# ---------------- Vue stream : frames + persistance ----------------

def test_stream_emet_reasoning_et_persiste(monkeypatch):
    from chat import providers as _pv
    from chat.models import AppSettings

    AppSettings.get_solo()  # mémoire : extraction coupée (pas d'appel provider réel)
    AppSettings.objects.filter(pk=1).update(memory_enabled=False)

    def fake_chat_stream(provider, model_id, messages, **kwargs):
        cb = kwargs.get("reasoning_callback")
        if cb is not None:
            cb("réflexion…")
        yield "Hello"

    monkeypatch.setattr(_pv, "chat_stream", fake_chat_stream)
    provider = _provider()
    conv = Conversation.objects.create(title="T", provider=provider, model_id="m")
    from django.urls import reverse

    resp = Client().get(reverse("chat:message_stream", args=[conv.pk]) + "?prompt=Bonjour")
    frames = b"".join(resp.streaming_content).decode()
    assert "event: reasoning" in frames
    assert "Hello" in frames and "event: done" in frames
    msg = Message.objects.filter(role="assistant").get()
    assert msg.reasoning == "réflexion…" and msg.content == "Hello"


def test_stream_sans_raisonnement_pas_de_frame(monkeypatch):
    from chat import providers as _pv
    from chat.models import AppSettings

    AppSettings.get_solo()
    AppSettings.objects.filter(pk=1).update(memory_enabled=False)

    monkeypatch.setattr(_pv, "chat_stream", lambda *a, **k: iter(["Hi"]))
    provider = _provider()
    conv = Conversation.objects.create(title="T", provider=provider, model_id="m")
    from django.urls import reverse

    resp = Client().get(reverse("chat:message_stream", args=[conv.pk]) + "?prompt=Bonjour")
    frames = b"".join(resp.streaming_content).decode()
    assert "event: reasoning" not in frames
    assert Message.objects.filter(role="assistant").get().reasoning == ""


# ---------------- Rendu + historique ----------------

def test_thread_affiche_details_et_historique_exclut():
    conv = Conversation.objects.create(title="T", system_prompt="Base.")
    Message.objects.create(conversation=conv, role="user", content="Question ?")
    Message.objects.create(conversation=conv, role="assistant", content="Réponse.", reasoning="Ma réflexion.")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert '<details class="reasoning">' in html and "Ma réflexion." in html
    from chat.views import _history

    hist = _history(conv)
    assert all("Ma réflexion." not in m["content"] for m in hist)
    assert hist[0] == {"role": "system", "content": "Base."}


def test_generate_reply_non_streame_raisonnement_vide(monkeypatch):
    from chat import providers as _pv
    from chat.views import _generate_reply

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "OK")
    provider = _provider()
    conv = Conversation.objects.create(title="T", provider=provider, model_id="m")
    Message.objects.create(conversation=conv, role="user", content="Q ?")
    ok, _ = _generate_reply(conv)
    assert ok is True
    assert Message.objects.filter(role="assistant").get().reasoning == ""
