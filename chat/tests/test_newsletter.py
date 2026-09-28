"""Bouton synthèse newsletter médiathèque : génération, .eml/.html, UI thread."""
import email as std_email

import pytest
from django.urls import reverse

from chat.models import Conversation, Export, Message, Provider
from chat.newsletter import mailto_href, split_newsletter

pytestmark = pytest.mark.django_db

FULL = (
    "Objet : Nouveautés romans de septembre\n\n"
    "## Bonjour\n\nVoici notre sélection.\n\n"
    "## À la une\n\n### Un roman marquant\n\nRésumé en quelques phrases.\n\n"
    "Pourquoi c'est intéressant : original.\n\n"
    "## En résumé\n\n- Point un.\n\n"
    "## Tendances à surveiller\n\n- Tendance une.\n\n"
    "Sources : [Hachette](https://hachette.fr/livre)\n\n"
    "---TEXTE---\n\nNouveautés romans de septembre. Voici notre sélection. Point un."
)


@pytest.fixture
def media_tmp(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _conv_with_provider():
    provider = Provider.objects.create(name="P-news", base_url="http://localhost:9/v1")
    return Conversation.objects.create(title="Livres", provider=provider, model_id="m")


def _assistant(conv, content="| Titre | Auteur |\n| --- | --- |\n| X | Y |"):
    return Message.objects.create(conversation=conv, role="assistant", content=content)


def _fake_complete_factory(captured):
    def _fake(provider, model_id, messages, **kwargs):
        captured["messages"] = messages
        captured["model"] = model_id
        return FULL

    return _fake


# ---- split / mailto (purs, sans réseau) ----

def test_split_newsletter_structure_complete():
    subject, md_part, text_part = split_newsletter(FULL)
    assert subject == "Nouveautés romans de septembre"
    assert "## Bonjour" in md_part and "---TEXTE---" not in md_part
    assert "Markdown" not in text_part and "##" not in text_part
    assert text_part.startswith("Nouveautés romans")


def test_split_newsletter_replis():
    subject, md_part, text_part = split_newsletter("## Titre sans objet\n\nContenu.")
    assert subject == "Titre sans objet"
    assert text_part == md_part
    long = "Objet : " + "x" * 200
    assert len(split_newsletter(long)[0]) == 80


def test_mailto_href_encode_et_tronque():
    href = mailto_href("Sélection & nouveautés", "Voir https://a.fr/x y.")
    assert href.startswith("mailto:?subject=")
    assert "%20" in href or "%C3%A9" in href
    assert "https%3A" in href
    long_href = mailto_href("S", "z" * 5000)
    assert len(long_href) < 2500
    assert ".eml" in long_href


# ---- builders eml / html ----

def test_build_eml_multipart_et_html_email_safe():
    from chat.exports.eml_gen import build_eml
    from chat.exports.html_gen import build_html

    payload = build_eml("Newsletter", FULL)
    from email.header import decode_header, make_header
    from email.policy import default as _default_policy

    parsed = std_email.message_from_bytes(payload, policy=_default_policy)
    assert str(make_header(decode_header(parsed["Subject"]))) == "Nouveautés romans de septembre"
    assert parsed["From"] and parsed["To"] and parsed["Date"]
    plain_body = parsed.get_body(preferencelist=("plain",))
    html_body = parsed.get_body(preferencelist=("html",))
    assert plain_body is not None and html_body is not None
    plain = plain_body.get_content()
    html_part = html_body.get_content()
    assert "Nouveautés romans" in plain and "##" not in plain
    assert "<style>" in html_part and "<table" not in html_part or "<h2" in html_part
    assert "<script>" not in html_part and "javascript:" not in html_part


def test_build_html_document_complet():
    from chat.exports.html_gen import build_html

    out = build_html("Sélection", FULL).decode("utf-8")
    assert out.startswith("<!DOCTYPE html>")
    assert "<title>Sélection</title>" in out
    assert "Bonjour" in out and "---TEXTE---" not in out


# ---- vue newsletter_create ----

def test_newsletter_create_genere_html_eml_sans_toucher_fil(client, media_tmp, monkeypatch):
    conv = _conv_with_provider()
    Message.objects.create(conversation=conv, role="user", content="Des romans ?")
    msg = _assistant(conv)
    captured = {}
    monkeypatch.setattr("chat.providers.chat_complete", _fake_complete_factory(captured))

    response = client.post(reverse("chat:newsletter_create", args=[msg.pk]))
    assert response.status_code == 302
    assert response["Location"] == reverse("chat:detail", args=[conv.pk])
    # Fil intact : aucun message ajouté.
    assert conv.messages.count() == 2
    assert sorted(Export.objects.values_list("format", flat=True)) == ["eml", "html"]
    # Prompt système newsletter bien utilisé.
    system = captured["messages"][0]["content"]
    assert "médiathèque" in system and "Objet" in system
    assert "tableau" in captured["messages"][1]["content"].lower() or "| Titre" in captured["messages"][1]["content"]


def test_newsletter_create_renouvelle_sans_doublons(client, media_tmp, monkeypatch):
    conv = _conv_with_provider()
    msg = _assistant(conv)
    captured = {}
    monkeypatch.setattr("chat.providers.chat_complete", _fake_complete_factory(captured))
    url = reverse("chat:newsletter_create", args=[msg.pk])
    client.post(url)
    client.post(url)
    assert Export.objects.filter(format="html").count() == 1
    assert Export.objects.filter(format="eml").count() == 1


def test_newsletter_create_refus_sans_reponse_ni_provider(client, media_tmp):
    conv = Conversation.objects.create(title="Sans provider")
    user_msg = Message.objects.create(conversation=conv, role="user", content="Hello")
    vide = Message.objects.create(conversation=conv, role="assistant", content="  ")
    for target in (user_msg, vide):
        response = client.post(reverse("chat:newsletter_create", args=[target.pk]))
        assert response.status_code == 302
    assert Export.objects.count() == 0
    plein = Message.objects.create(conversation=conv, role="assistant", content="Du texte.")
    response = client.post(reverse("chat:newsletter_create", args=[plein.pk]))
    assert response.status_code == 302
    assert Export.objects.count() == 0


def test_newsletter_create_erreur_provider_affichee(client, media_tmp, monkeypatch):
    from chat import providers

    conv = _conv_with_provider()
    msg = _assistant(conv)

    def _boom(*args, **kwargs):
        raise providers.ProviderError("Modèle indisponible.")

    monkeypatch.setattr("chat.providers.chat_complete", _boom)
    response = client.post(reverse("chat:newsletter_create", args=[msg.pk]), follow=True)
    assert response.status_code == 200
    assert Export.objects.count() == 0
    assert "Newsletter impossible" in response.content.decode()


def test_thread_affiche_3_boutons_canal(client, media_tmp):
    conv = _conv_with_provider()
    _assistant(conv, content="## Romans\n\nVoir [Hachette](https://hachette.fr).")
    html = client.get(reverse("chat:detail", args=[conv.pk])).content.decode()
    for channel in ("mailto", "html", "eml"):
        assert f"/newsletter/{channel}/" in html
    assert "postNewsletterMailto" in html and "postNewsletterHTML" in html and "postNewsletterEML" in html
    assert "mailto:" not in html


def test_chaque_clic_regenere_version_adaptee(client, media_tmp, monkeypatch):
    conv = _conv_with_provider()
    msg = _assistant(conv, content="## Romans\n\nContenu.")
    calls = []

    def _fake(provider, model_id, messages, **kwargs):
        calls.append(messages[0]["content"])
        system = messages[0]["content"]
        if "texte brut" in system and "1200" in system:
            return "Objet : Court\n\nCorps court brut."
        if "e-mail" in system or "email" in system:
            return "Objet : Riche\n\n## Titre\n\nContenu riche."
        return FULL

    monkeypatch.setattr("chat.providers.chat_complete", _fake)
    base = reverse("chat:newsletter_channel", args=[msg.pk, "mailto"]).rsplit("mailto/", 1)[0]
    mailto = client.post(base + "mailto/", HTTP_HX_REQUEST="true")
    assert mailto.status_code == 200
    assert mailto.json()["mailto"].startswith("mailto:?subject=Court")
    assert "##" not in mailto.json()["mailto"]
    html_resp = client.post(base + "html/", HTTP_HX_REQUEST="true")
    assert html_resp.status_code == 200
    assert "Contenu riche" in html_resp.json()["html"]
    eml_resp = client.post(base + "eml/", HTTP_HX_REQUEST="true")
    assert eml_resp.status_code == 200
    download = client.get(eml_resp.json()["download"])
    assert download.status_code == 200
    # 3 clics = 3 générations, prompts distincts.
    assert len(calls) == 3 and len(set(calls)) == 3
    assert conv.messages.count() == 1


def test_canal_inconnu_404_et_gardes(client, media_tmp):
    conv = _conv_with_provider()
    msg = _assistant(conv)
    assert client.post(reverse("chat:newsletter_channel", args=[msg.pk, "sms"])).status_code == 404
    user_msg = Message.objects.create(conversation=conv, role="user", content="Hello")
    response = client.post(
        reverse("chat:newsletter_channel", args=[user_msg.pk, "mailto"]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 400
    assert "réponse" in response.json()["error"]


def test_prompts_canal_contraintes_et_troncature_source(monkeypatch):
    from chat.newsletter import CHANNEL_SYSTEMS, NEWSLETTER_MAX_SOURCE_CHARS, build_newsletter_for_channel

    assert "1200" in CHANNEL_SYSTEMS["short"] and "Markdown" in CHANNEL_SYSTEMS["short"]
    assert "https://" in CHANNEL_SYSTEMS["rich"] and "tableau" in CHANNEL_SYSTEMS["rich"].lower()
    assert "Tendances" in CHANNEL_SYSTEMS["full"]
    seen = {}

    def _fake(provider, model_id, messages, **kwargs):
        seen["user_len"] = len(messages[1]["content"])
        return "Objet : X\n\n---TEXTE---\n\nY"

    monkeypatch.setattr("chat.providers.chat_complete", _fake)
    conv = _conv_with_provider()
    build_newsletter_for_channel(conv, "z" * (NEWSLETTER_MAX_SOURCE_CHARS + 5000), "short")
    assert seen["user_len"] <= NEWSLETTER_MAX_SOURCE_CHARS + 100
    import pytest as _pytest

    with _pytest.raises(ValueError):
        build_newsletter_for_channel(conv, "texte", "sms")


def test_export_create_html_eml_generiques(client, media_tmp):
    conv = Conversation.objects.create(title="T")
    msg = Message.objects.create(conversation=conv, role="assistant", content="## A\n\nT.")
    for fmt in ("html", "eml"):
        response = client.post(reverse("chat:export_create", args=[msg.pk, fmt]))
        assert response.status_code == 302
    assert Export.objects.filter(format="html").exists()
    assert Export.objects.filter(format="eml").exists()
