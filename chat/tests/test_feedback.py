import pytest
from django.test import Client
from django.urls import reverse

from chat.models import Conversation, Message

pytestmark = pytest.mark.django_db


def _assistant(content="Réponse de test.") -> Message:
    conv = Conversation.objects.create(title="T")
    return Message.objects.create(conversation=conv, role="assistant", content=content)


def test_feedback_up_puis_annulation():
    message = _assistant()
    url = reverse("chat:message_feedback", args=[message.pk])
    Client().post(url, {"value": "up"}, HTTP_HX_REQUEST="true")
    message.refresh_from_db()
    assert message.feedback == "up"
    Client().post(url, {"value": "up"}, HTTP_HX_REQUEST="true")
    message.refresh_from_db()
    assert message.feedback == ""


def test_feedback_bascule_up_vers_down():
    message = _assistant()
    url = reverse("chat:message_feedback", args=[message.pk])
    Client().post(url, {"value": "up"}, HTTP_HX_REQUEST="true")
    Client().post(url, {"value": "down"}, HTTP_HX_REQUEST="true")
    message.refresh_from_db()
    assert message.feedback == "down"


def test_feedback_valeur_invalide_400():
    message = _assistant()
    response = Client().post(
        reverse("chat:message_feedback", args=[message.pk]),
        {"value": "love"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 400


def test_feedback_question_refusee_400():
    conv = Conversation.objects.create(title="T")
    question = Message.objects.create(conversation=conv, role="user", content="?")
    response = Client().post(
        reverse("chat:message_feedback", args=[question.pk]),
        {"value": "up"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 400


def test_barre_actions_rendue_sous_reponse():
    message = _assistant()
    html = Client().get(f"/{message.conversation.pk}/").content.decode()
    for label in ("Copier la réponse", "Bonne réponse", "Mauvaise réponse", "Régénérer la réponse"):
        assert label in html
    assert reverse("chat:message_feedback", args=[message.pk]) in html


def test_pouce_actif_mis_en_avant():
    message = _assistant()
    message.feedback = "up"
    message.save(update_fields=["feedback"])
    html = Client().get(f"/{message.conversation.pk}/").content.decode()
    assert 'msg-action text-accent' in html
