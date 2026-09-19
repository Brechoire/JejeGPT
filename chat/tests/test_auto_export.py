import pytest
from django.urls import reverse

from chat.exports.service import detect_export_request
from chat.models import Conversation, Export, Message, Provider

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_tmp(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Génère moi un fichier pdf qui liste les commandes django", "pdf"),
        ("crée un document word sur les médiathèques", "docx"),
        ("sors-moi un fichier excel des prêts", "xlsx"),
        ("génère un powerpoint sur le tri", "pptx"),
        ("exporte-moi ça en docx stp", "docx"),
        ("fais-moi un tableur des inscrits", "xlsx"),
        ("prépare une présentation sur le tri", "pptx"),
        ("télécharge le rapport en PDF", "pdf"),
        ("c'est quoi un pdf ?", None),
        ("génère un résumé du chapitre", None),
        ("parle-moi de python-pptx", None),
        ("", None),
    ],
)
def test_detect_export_request(text, expected):
    assert detect_export_request(text) == expected


def _conversation() -> Conversation:
    provider = Provider.objects.create(name="T", base_url="http://localhost:9/v1")
    return Conversation.objects.create(title="Conv", provider=provider, model_id="m")


def test_send_avec_intention_cree_export_et_lien(client, media_tmp, monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "# Commandes Django\n\n- runserver\n")
    conv = _conversation()
    response = client.post(
        f"/conversations/{conv.pk}/send/",
        {"content": "Génère moi un fichier pdf qui liste les commandes django"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    export = Export.objects.get()
    assert export.format == "pdf"
    assert export.size > 0
    assert export.get_absolute_url() in response.content.decode()


def test_send_sans_intention_ne_cree_rien(client, media_tmp, monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "Voici un résumé.")
    conv = _conversation()
    client.post(f"/conversations/{conv.pk}/send/", {"content": "Résume ce chapitre"}, HTTP_HX_REQUEST="true")
    assert Export.objects.count() == 0


def test_send_en_erreur_provider_ne_cree_rien(client, media_tmp, monkeypatch):
    from chat import providers as _pv

    def boom(*a, **k):
        raise _pv.ProviderError("coupure")

    monkeypatch.setattr(_pv, "chat_complete", boom)
    conv = _conversation()
    client.post(
        f"/conversations/{conv.pk}/send/",
        {"content": "Génère un pdf stp"},
        HTTP_HX_REQUEST="true",
    )
    assert Export.objects.count() == 0


def test_regenerer_remplace_reponse_et_son_export(client, media_tmp, monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "# Titre\n\nContenu.")
    conv = _conversation()
    message = Message.objects.create(conversation=conv, role="user", content="Génère un pdf stp")
    client.post(f"/conversations/{conv.pk}/send/", {"content": "Génère un pdf stp"}, HTTP_HX_REQUEST="true")
    assert Export.objects.count() == 1
    client.post(reverse("chat:message_regenerate_from", args=[message.pk]), HTTP_HX_REQUEST="true")
    assert Export.objects.count() == 0
    assert conv.messages.filter(role="assistant").exclude(content="").exists()


def test_auto_export_idempotent_par_format(client, media_tmp):
    from chat.views import _maybe_auto_export

    conv = _conversation()
    Message.objects.create(conversation=conv, role="assistant", content="# Titre\n\nContenu.")
    _maybe_auto_export(conv, "Génère un pdf stp")
    _maybe_auto_export(conv, "Génère un pdf stp")
    assert Export.objects.filter(format="pdf").count() == 1
    _maybe_auto_export(conv, "Génère aussi une version docx")
    assert Export.objects.count() == 2


def test_stream_avec_intention_cree_export(client, media_tmp, monkeypatch):
    from chat import providers as _pv

    monkeypatch.setattr(_pv, "chat_stream", lambda *a, **k: iter(["# Commandes\n\n- runserver\n"]))
    conv = _conversation()
    response = client.get(f"/conversations/{conv.pk}/stream/?prompt=Génère un pdf des commandes")
    assert response.status_code == 200
    b"".join(response.streaming_content)
    export = Export.objects.get()
    assert export.format == "pdf"
