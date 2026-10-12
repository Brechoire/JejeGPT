"""Enrichissements V1 : canvas, surlignage, artifacts, fork/versions, upload,
voix (hooks), slash/snippets, recherche, comparo A-B, follow-ups.
"""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from chat import providers
from chat.models import Attachment, Conversation, Document, Provider
from chat.views import _followups_for


def _conv(**kw):
    defaults = {"title": "T"}
    defaults.update(kw)
    return Conversation.objects.create(**defaults)


@pytest.mark.django_db
def test_pin_toggle():
    c = _conv()
    m = c.messages.create(role="assistant", content="r")
    client = Client()
    client.post(f"/messages/{m.pk}/pin/", HTTP_HX_REQUEST="true")
    m.refresh_from_db()
    assert m.pinned is True
    html = client.get(f"/{c.pk}/").content.decode()
    assert "Épinglés" in html
    client.post(f"/messages/{m.pk}/pin/", HTTP_HX_REQUEST="true")
    m.refresh_from_db()
    assert m.pinned is False


@pytest.mark.django_db
def test_fork_copies_history():
    c = _conv(title="Orig")
    c.messages.create(role="user", content="q1")
    a1 = c.messages.create(role="assistant", content="r1")
    c.messages.create(role="user", content="q2")
    resp = Client().post(f"/messages/{a1.pk}/fork/")
    assert resp.status_code == 302
    fork = Conversation.objects.exclude(pk=c.pk).get()
    assert "(fork)" in fork.title
    assert list(fork.messages.values_list("content", flat=True)) == ["q1", "r1"]


@pytest.mark.django_db
def test_retry_with_creates_sibling_version(monkeypatch):
    go = Provider.objects.get(name="OpenCode Go")
    go.cached_models.create(provider=go, model_id="alt-model")
    c = _conv(provider=go, model_id="m")
    q = c.messages.create(role="user", content="q")
    c.messages.create(role="assistant", content="v1")
    monkeypatch.setattr(
        providers, "chat_complete", lambda *a, **k: "v2-alt")
    resp = Client().post(
        f"/messages/{q.pk}/retry-with/",
        {"provider_id": str(go.pk), "model_id": "alt-model"},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    versions = list(c.messages.filter(parent=q).order_by("version_no"))
    assert len(versions) == 1
    assert versions[0].content == "v2-alt"
    assert versions[0].model_label == "OpenCode Go / alt-model"
    assert "V2" in resp.content.decode() or "versions" in resp.content.decode()


@pytest.mark.django_db
def test_retry_with_guards():
    c = _conv()
    q = c.messages.create(role="user", content="q")
    # Erreurs renvoyées en 200 + bannière (HTMX ne swap que le 2xx).
    resp = Client().post(
        f"/messages/{q.pk}/retry-with/",
        {"provider_id": "9999", "model_id": "x"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "Provider invalide" in resp.content.decode()
    go = Provider.objects.get(name="OpenCode Go")
    resp = Client().post(
        f"/messages/{q.pk}/retry-with/",
        {"provider_id": str(go.pk), "model_id": "modele-inexistant"},
        HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "Modèle inconnu" in resp.content.decode()


@pytest.mark.django_db
def test_retry_with_provider_error_shows_notice(monkeypatch):
    go = Provider.objects.get(name="OpenCode Go")
    go.cached_models.create(provider=go, model_id="alt-model")
    c = _conv(provider=go, model_id="m")
    q = c.messages.create(role="user", content="q")
    c.messages.create(role="assistant", content="v1")

    def _boom(*a, **k):
        raise providers.ProviderError("quota épuisé")

    monkeypatch.setattr(providers, "chat_complete", _boom)
    resp = Client().post(
        f"/messages/{q.pk}/retry-with/",
        {"provider_id": str(go.pk), "model_id": "alt-model"},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "quota épuisé" in html
    assert c.messages.count() == 2  # aucune version créée en échec


@pytest.mark.django_db
def test_retry_dropdown_markup():
    from chat.models import LLMModel
    go = Provider.objects.get(name="OpenCode Go")
    LLMModel.objects.create(provider=go, model_id="alt-model")
    c = _conv(provider=go, model_id="m")
    c.messages.create(role="user", content="q")
    c.messages.create(role="assistant", content="r")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "Rejouer avec un autre modèle" in html
    assert "hx-indicator" in html
    assert "max-h-64 overflow-y-auto" in html
    assert "alt-model" in html


@pytest.mark.django_db
def test_document_open_save_action(monkeypatch):
    c = _conv(title="Doc conv")
    m = c.messages.create(role="assistant", content="contenu graine")
    client = Client()
    resp = client.post(f"/conversations/{c.pk}/document/ouvrir/",
                       {"message_id": str(m.pk)}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    doc = Document.objects.get(conversation=c)
    assert doc.content == "contenu graine"
    # sauvegarde manuelle
    resp = client.post(f"/documents/{doc.pk}/sauver/",
                       {"title": "Mon doc", "content": "edit"})
    assert resp.status_code == 200
    assert json.loads(resp.content.decode())["version"] == 2
    # action IA inconnue
    assert client.post(f"/documents/{doc.pk}/action/",
                       {"action": "nope"}).status_code == 400
    # action IA sans provider
    assert client.post(f"/documents/{doc.pk}/action/",
                       {"action": "raccourcir"}).status_code == 400


@pytest.mark.django_db
def test_document_action_ok(monkeypatch):
    go = Provider.objects.get(name="OpenCode Go")
    c = _conv(provider=go, model_id="m")
    doc = Document.objects.create(conversation=c, title="D", content="long texte")
    monkeypatch.setattr(providers, "chat_complete", lambda *a, **k: "court")
    resp = Client().post(f"/documents/{doc.pk}/action/", {"action": "raccourcir"})
    assert resp.status_code == 200
    payload = json.loads(resp.content.decode())
    assert payload["content"] == "court"
    doc.refresh_from_db()
    assert doc.content == "court"


@pytest.mark.django_db
def test_attachment_upload_and_delete():
    c = _conv()
    client = Client()
    up = SimpleUploadedFile("notes.txt", b"ligne1\nligne2",
                            content_type="text/plain")
    resp = client.post(f"/conversations/{c.pk}/pieces/", {"file": up})
    assert resp.status_code == 302
    att = Attachment.objects.get(conversation=c)
    assert "ligne1" in att.excerpt
    html = client.get(f"/{c.pk}/").content.decode()
    assert "notes.txt" in html
    resp = client.post(f"/pieces/{att.pk}/supprimer/")
    assert resp.status_code == 302
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_attachment_rejects_bad_format_and_size():
    c = _conv()
    client = Client()
    bad = SimpleUploadedFile("x.pdf", b"%PDF", content_type="application/pdf")
    client.post(f"/conversations/{c.pk}/pieces/", {"file": bad})
    assert Attachment.objects.count() == 0
    big = SimpleUploadedFile("gros.txt", b"a" * (513 * 1024),
                             content_type="text/plain")
    client.post(f"/conversations/{c.pk}/pieces/", {"file": big})
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_attachments_context_injected():
    from chat.views import _history
    c = _conv(system_prompt="SYS")
    Attachment.objects.create(conversation=c, file_name="n.txt", size=3,
                              excerpt="EXTRAIT-JOINT")
    hist = _history(c)
    assert hist[0]["role"] == "system"
    assert "EXTRAIT-JOINT" in hist[0]["content"]
    assert "SYS" in hist[0]["content"]


@pytest.mark.django_db
def test_global_search():
    c = _conv(title="Recette carbonara")
    c.messages.create(role="user", content="parle-moi des pates")
    client = Client()
    html = client.get("/recherche/?q=carbonara").content.decode()
    assert "Recette carbonara" in html
    html = client.get("/recherche/?q=pates").content.decode()
    assert "pates" in html
    html = client.get("/recherche/?q=x").content.decode()
    assert "2 lettres" in html


@pytest.mark.django_db
def test_snippets_api():
    c = _conv()
    data = json.loads(Client().get("/api/snippets/").content.decode())
    assert any(s["cmd"] == "/resume" for s in data["snippets"])
    data = json.loads(
        Client().get(f"/api/snippets/?conversation={c.pk}").content.decode())
    assert len(data["snippets"]) >= 5


def test_artifact_preview_buttons():
    from chat.md import render_markdown
    html = render_markdown("```html\n<b>x</b>\n```")
    assert "previewArtifact(this)" in html
    assert "downloadArtifact(this)" in html
    assert "artifact-preview" in html
    plain = render_markdown("```python\nprint(1)\n```")
    assert "previewArtifact" not in plain
    assert "copyCode(this)" in plain


def test_followups_heuristics():
    chips = _followups_for("```python\nprint(1)\n```\n| a | b |\n" + "x" * 900)
    assert len(chips) == 3
    assert chips[0] == "Résume en 5 points clés"
    assert _followups_for("") == []


@pytest.mark.django_db
def test_detail_hooks_all_features():
    c = _conv()
    c.messages.create(role="user", content="q")
    c.messages.create(role="assistant", content="```html\n<b>x</b>\n```")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "canvas-wrap" in html
    assert "snippets-data" in html
    assert "slash-popup" in html
    assert "dictateToComposer" in html
    assert "speakText" in html
    assert "attachments-panel" in html
    assert "global_search" in html or "/recherche/" in html
    assert "selection-toolbar" in html
    assert "bindSelectionToolbar" in html
    assert "previewArtifact" in html
