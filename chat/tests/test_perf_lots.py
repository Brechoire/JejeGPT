"""Perf Lots A/B/C : head allégé, caches, anti-CLS.

Verrouille les gains sans figer les perfs (pas de seuils temporels fragiles) :
présence des assets, invalidation des caches, requêtes épargnées.
"""
from pathlib import Path

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client

from chat.models import AppSettings, Conversation

pytestmark = pytest.mark.django_db

BASE = Path(settings.BASE_DIR)


def test_tailwind_precompile_remplace_play_cdn():
    base = (BASE / "templates" / "base.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in base
    assert "tailwind.config" not in base
    css = BASE / "static" / "css" / "tailwind.css"
    assert css.exists()
    compiled = css.read_text(encoding="utf-8")
    for cls in (".flex", ".truncate", ".text-ink", ".bg-accent"):
        assert cls in compiled


def test_libs_lourdes_par_page():
    base = (BASE / "templates" / "base.html").read_text(encoding="utf-8")
    assert "{% block extra_head %}" in base
    # Aucune URL de lib lourde dans le head global (on vérifie les fragments
    # d'URL versionnés, pas les mots cités par le commentaire explicatif).
    for url in ("abcjs@", "Tone.js", "marked.umd", "purify.min", "highlight.min",
                "score-viewer.js"):
        assert url not in base
    detail = (BASE / "templates" / "chat" / "detail.html").read_text(encoding="utf-8")
    for lib in ("highlight.js", "marked", "purify"):
        assert lib in detail
    for lib in ("abcjs", "tone", "score-viewer"):
        assert lib not in detail
    demo = (BASE / "templates" / "chat" / "score_demo.html").read_text(encoding="utf-8")
    for lib in ("abcjs", "tone", "score-viewer"):
        assert lib in demo
    for lib in ("unpkg.com", "cdnjs.cloudflare.com", "cdn.jsdelivr.net"):
        assert "preconnect" in base and lib in base


def test_detail_sans_libs_demo():
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    for lib in ("abcjs", "tone", "score-viewer"):
        assert lib not in html
    assert "tailwind.css" in html


def test_markdown_cache_invalide_a_l_edition():
    from chat.views import _rendered_html

    conv = Conversation.objects.create(title="T")
    m = conv.messages.create(role="assistant", content="Bonjour **monde**")
    cache.clear()
    first = _rendered_html(m)
    assert "<strong>monde</strong>" in first
    # 2e appel : servi du cache (même objet reconstruit depuis la DB).
    from chat.models import Message

    assert _rendered_html(Message.objects.get(pk=m.pk)) == first
    # Édition : contenu différent → clé différente → HTML à jour.
    m.content = "Adieu **lune**"
    m.save(update_fields=["content"])
    second = _rendered_html(Message.objects.get(pk=m.pk))
    assert "<strong>lune</strong>" in second and "monde" not in second


def test_appsettings_cache_invalide_au_save():
    cache.clear()
    solo = AppSettings.get_solo()
    solo.text_scale = 100
    solo.save(update_fields=["text_scale"])
    assert AppSettings.get_cached().text_scale == 100
    solo.text_scale = 125
    solo.save(update_fields=["text_scale"])
    # Invalidation immédiate : pas de valeur périmée après écriture.
    assert AppSettings.get_cached().text_scale == 125


def test_images_aspect_ratio_reserve():
    css = (BASE / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert css.count("aspect-ratio: 16 / 10;") >= 2
