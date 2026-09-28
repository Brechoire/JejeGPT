"""Régénération : ancre question/réponse + indicateur de chargement."""
import pytest
from django.test import Client

from chat.models import Conversation, Provider


@pytest.fixture
def conv_mono(db, monkeypatch):
    """Conversation à un échange, provider mocké (aucun appel réseau)."""
    calls = {"n": 0}

    def fake_complete(*args, **kwargs):
        calls["n"] += 1
        return f"réponse regen {calls['n']}"

    import chat.providers

    monkeypatch.setattr(chat.providers, "chat_complete", fake_complete)
    provider = Provider.objects.create(name="regen-prov", base_url="http://localhost:1/v1")
    conv = Conversation.objects.create(
        title="T", provider=provider, model_id="m", show_images=False
    )
    return conv


def test_regenerate_from_assistant_uses_previous_question(conv_mono):
    q = conv_mono.messages.create(role="user", content="Explique la photosynthèse")
    old = conv_mono.messages.create(role="assistant", content="vieille réponse")
    resp = Client().post(f"/messages/{old.pk}/regenerate/")
    assert resp.status_code == 200
    remaining = list(
        conv_mono.messages.order_by("created_at", "id").values_list("role", "content")
    )
    assert remaining == [("user", "Explique la photosynthèse"), ("assistant", "réponse regen 1")]
    assert "réponse regen 1" in resp.content.decode()
    assert q.pk is not None


def test_regenerate_from_question_deletes_everything_below(conv_mono):
    q1 = conv_mono.messages.create(role="user", content="Question 1")
    conv_mono.messages.create(role="assistant", content="Réponse 1")
    conv_mono.messages.create(role="user", content="Question 2")
    conv_mono.messages.create(role="assistant", content="Réponse 2")
    resp = Client().post(f"/messages/{q1.pk}/regenerate/")
    assert resp.status_code == 200
    remaining = list(
        conv_mono.messages.order_by("created_at", "id").values_list("role", "content")
    )
    assert remaining == [("user", "Question 1"), ("assistant", "réponse regen 1")]
    body = resp.content.decode()
    assert "Réponse 1" not in body and "Question 2" not in body and "Réponse 2" not in body


def test_regenerate_from_answer_deletes_everything_below(conv_mono):
    conv_mono.messages.create(role="user", content="Question 1")
    a1 = conv_mono.messages.create(role="assistant", content="Réponse 1")
    conv_mono.messages.create(role="user", content="Question 2")
    conv_mono.messages.create(role="assistant", content="Réponse 2")
    resp = Client().post(f"/messages/{a1.pk}/regenerate/")
    assert resp.status_code == 200
    remaining = list(
        conv_mono.messages.order_by("created_at", "id").values_list("role", "content")
    )
    assert remaining == [("user", "Question 1"), ("assistant", "réponse regen 1")]


def test_regenerate_from_assistant_without_question_400(db):
    conv = Conversation.objects.create(title="T")
    lonely = conv.messages.create(role="assistant", content="orpheline")
    resp = Client().post(f"/messages/{lonely.pk}/regenerate/")
    assert resp.status_code == 400
    lonely.refresh_from_db()
    assert lonely.content == "orpheline"


def test_regenerate_top_button_200(conv_mono):
    conv_mono.messages.create(role="user", content="Explique la gravité")
    conv_mono.messages.create(role="assistant", content="vieille")
    resp = Client().post(f"/conversations/{conv_mono.pk}/regenerate/")
    assert resp.status_code == 200
    assert "réponse regen 1" in resp.content.decode()


def test_conversation_update_hx_returns_options_without_reload(conv_mono):
    conv_mono.show_images = False
    conv_mono.save()
    resp = Client().post(
        f"/conversations/{conv_mono.pk}/update/",
        {"show_images": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    conv_mono.refresh_from_db()
    assert conv_mono.show_images is True
    body = resp.content.decode()
    # Bloc d'options à jour : le toggle propose maintenant de couper (0)
    assert "composer-opts" in body
    assert '{"show_images": "0"}' in body
    # POST classique : toujours une redirection (formulaires réglages)
    resp2 = Client().post(f"/conversations/{conv_mono.pk}/update/", {"show_images": "0"})
    assert resp2.status_code == 302


def test_regenerate_forms_have_loading_indicator(conv_mono):
    conv_mono.messages.create(role="user", content="Explique la gravité")
    conv_mono.messages.create(role="assistant", content="réponse")
    html = Client().get(f"/{conv_mono.pk}/").content.decode()
    assert 'id="regen-indicator"' in html
    # 2 formulaires régénérer du thread + 1 édition + bouton ↻ d'en-tête
    assert html.count('hx-indicator="#regen-indicator"') == 4
    assert html.count('hx-disabled-elt="find button"') == 4


def test_edit_form_usable(conv_mono):
    conv_mono.messages.create(role="user", content="Explique la gravité")
    conv_mono.messages.create(role="assistant", content="réponse")
    html = Client().get(f"/{conv_mono.pk}/").content.decode()
    # Fenêtre élargie en édition, zone haute avec autogrow, validation explicite
    assert "w-full !max-w-full sm:!max-w-full" in html
    assert 'rows="6"' in html
    assert "autogrow($el)" in html
    assert "Ctrl+Entrée pour valider" in html
    assert "Enregistrer et régénérer" in html
