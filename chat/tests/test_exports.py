import uuid

import pytest
from django.conf import settings
from django.core.files.base import ContentFile

from chat.exports.core import check_quota, check_size, safe_name
from chat.models import Conversation, Export, Message

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_tmp(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _message() -> Message:
    conv = Conversation.objects.create(title="Test")
    return Message.objects.create(conversation=conv, role="assistant", content="contenu")


def _export(message: Message, size: int = 10) -> Export:
    return Export.objects.create(
        message=message,
        format="pdf",
        file=ContentFile(b"x" * size, name="test.pdf"),
        file_name="test.pdf",
        size=size,
    )


def test_safe_name_garde_accents_et_nettoie():
    assert safe_name("Rapport médiathèque — 2026!", "pdf") == "Rapport-médiathèque-2026.pdf"
    assert safe_name("../../etc/passwd", "docx") == "etc-passwd.docx"
    assert "/" not in safe_name("../../etc/passwd", "docx")
    assert ".." not in safe_name("../../etc/passwd", "docx")
    assert safe_name("   ", "xlsx") == "export.xlsx"


def test_check_size_et_quota():
    check_size(1024)
    with pytest.raises(Exception):
        check_size(0)
    with pytest.raises(Exception):
        check_size(settings.JEJEGPT_EXPORTS_MAX_BYTES + 1)


def test_quota_bloque_au_dela(media_tmp):
    message = _message()
    _export(message, size=settings.JEJEGPT_EXPORTS_QUOTA_BYTES - 100)
    check_quota(50)
    with pytest.raises(Exception):
        check_quota(200)


def test_export_token_unique_et_url(media_tmp):
    message = _message()
    e1 = _export(message)
    e2 = _export(message)
    assert e1.download_token != e2.download_token
    assert uuid.UUID(str(e1.download_token))
    assert str(e1.download_token) in str(e1.get_absolute_url())


def test_suppression_conversation_cascade_export(media_tmp):
    message = _message()
    export = _export(message)
    path = settings.MEDIA_ROOT / export.file.name
    assert path.exists()
    message.conversation.delete()
    assert not Export.objects.filter(pk=export.pk).exists()


def test_signal_post_delete_export_supprime_fichier(media_tmp, django_capture_on_commit_callbacks):
    message = _message()
    export = _export(message)
    path = settings.MEDIA_ROOT / export.file.name
    assert path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        export.delete()
    assert not Export.objects.filter(pk=export.pk).exists()
    assert not path.exists()


def test_signal_post_delete_message_supprime_fichier_via_cascade(
    media_tmp, django_capture_on_commit_callbacks
):
    message = _message()
    export = _export(message)
    path = settings.MEDIA_ROOT / export.file.name
    assert path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        message.delete()
    assert not Export.objects.filter(pk=export.pk).exists()
    assert not path.exists()
