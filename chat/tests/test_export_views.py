import uuid

import pytest
from django.urls import reverse

from chat.exports.core import safe_name
from chat.models import Conversation, Export, Message

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_tmp(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _assistant_message(content="# Titre\n\nDu contenu avec un tableau.\n") -> Message:
    conv = Conversation.objects.create(title="Conversation de test")
    return Message.objects.create(conversation=conv, role="assistant", content=content)


def test_export_create_pdf_redirige_vers_telechargement(client, media_tmp):
    message = _assistant_message()
    url = reverse("chat:export_create", args=[message.pk, "pdf"])
    response = client.post(url)
    assert response.status_code == 302
    assert Export.objects.count() == 1
    export = Export.objects.get()
    assert export.format == "pdf"
    assert response["Location"].endswith(str(export.download_token) + "/")
    assert export.file_name == safe_name(message.conversation.title, "pdf")


def test_export_create_rejette_format_inconnu(client, media_tmp):
    message = _assistant_message()
    url = reverse("chat:export_create", args=[message.pk, "odt"])
    response = client.post(url)
    assert response.status_code == 302
    assert Export.objects.count() == 0


def test_export_download_attachment(client, media_tmp):
    message = _assistant_message()
    client.post(reverse("chat:export_create", args=[message.pk, "docx"]))
    export = Export.objects.get()
    response = client.get(export.get_absolute_url())
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert export.file_name in response["Content-Disposition"]
    assert b"".join(response.streaming_content)[:2] == b"PK"


def test_export_download_token_inconnu_404(client):
    response = client.get(reverse("chat:export_download", kwargs={"token": uuid.uuid4()}))
    assert response.status_code == 404


def test_export_create_message_inexistant_404(client):
    response = client.post(reverse("chat:export_create", args=[999999, "pdf"]))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("fmt", "magic"),
    [("pdf", b"%PDF-"), ("docx", b"PK"), ("xlsx", b"PK"), ("pptx", b"PK")],
)
def test_recette_bout_en_bout_tous_formats(client, media_tmp, fmt, magic):
    message = _assistant_message()
    created = client.post(reverse("chat:export_create", args=[message.pk, fmt]))
    assert created.status_code == 302
    export = Export.objects.get(format=fmt)
    assert export.size > 0
    downloaded = client.get(export.get_absolute_url())
    assert downloaded.status_code == 200
    assert downloaded["Content-Disposition"].startswith("attachment;")
    assert b"".join(downloaded.streaming_content).startswith(magic)


def test_onglet_donnees_affiche_exports_recents(client, media_tmp):
    message = _assistant_message()
    client.post(reverse("chat:export_create", args=[message.pk, "pdf"]))
    export = Export.objects.get()
    response = client.get(reverse("chat:settings_tab", args=["donnees"]))
    assert response.status_code == 200
    assert export.file_name in response.content.decode()
