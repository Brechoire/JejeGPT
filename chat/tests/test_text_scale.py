import pytest
from django.test import Client
from django.urls import reverse

from chat.context_processors import text_scale as text_scale_ctx
from chat.models import AppSettings, Conversation

pytestmark = pytest.mark.django_db


def test_scale_factor_paliers_et_repli():
    assert AppSettings(text_scale=87).scale_factor() == 87
    assert AppSettings(text_scale=100).scale_factor() == 100
    assert AppSettings(text_scale=112).scale_factor() == 112
    assert AppSettings(text_scale=125).scale_factor() == 125
    assert AppSettings(text_scale=999).scale_factor() == 100
    assert AppSettings(text_scale=0).scale_factor() == 100


def test_context_processor_defaut_et_persisté():
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    assert text_scale_ctx(request) == {"text_scale": 100}
    solo = AppSettings.get_solo()
    solo.text_scale = 125
    solo.save(update_fields=["text_scale"])
    assert text_scale_ctx(request) == {"text_scale": 125}


def test_html_reflete_palier():
    solo = AppSettings.get_solo()
    solo.text_scale = 112
    solo.save(update_fields=["text_scale"])
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert '<html lang="fr" style="font-size:112%">' in html


def test_save_paliers_et_invalide_conserve():
    client = Client()
    for value in ("87", "100", "112", "125"):
        client.post(reverse("chat:settings_save"), {"text_scale": value})
        AppSettings.get_solo().refresh_from_db()
        assert AppSettings.get_solo().text_scale == int(value)
    client.post(reverse("chat:settings_save"), {"text_scale": "999"})
    assert AppSettings.get_solo().text_scale == 125
    client.post(reverse("chat:settings_save"), {"text_scale": "grand"})
    assert AppSettings.get_solo().text_scale == 125


def test_section_taille_texte_onglet_general():
    html = Client().get(reverse("chat:settings_tab", kwargs={"tab": "general"})).content.decode()
    assert "Taille du texte" in html
    for label in ("Compact", "Normal", "Large", "XL"):
        assert label in html
    assert 'name="text_scale"' in html


def test_css_typo_en_rem():
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert "font-size: 1rem" in css
    assert "font-size: 1.625rem" in css  # h1 26px à 100 %
    assert "font-size: 0.8125rem" in css  # msg-time 13px à 100 %
    import re

    assert not re.search(r"font-size:\s*\d+px", css)


def test_choix_applique_au_clic():
    html = Client().get(reverse("chat:settings_tab", kwargs={"tab": "general"})).content.decode()
    assert 'onchange="this.form.submit()"' in html
    assert "Appliqué immédiatement au clic" in html


def test_aucun_texte_px_fixe_dans_templates():
    import re
    from pathlib import Path

    from django.conf import settings

    restes = []
    for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.findall(r"text-\[\d+px\]", line):
                restes.append(f"{path.name}:{i}:{match}")
    assert restes == []
