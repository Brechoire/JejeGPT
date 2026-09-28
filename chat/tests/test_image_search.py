"""Images web illustratives : heuristique, markdown, rendu galerie."""
from django.test import override_settings

from chat import image_search
from chat.image_search import ImageResult, build_query, images_markdown, needs_images
from chat.md import render_markdown


def test_needs_images_explicite():
    assert needs_images("Montre-moi des photos du Nautilon") is True
    assert needs_images("À quoi ressemblent les ruines sous-marines ?") is True


def test_needs_images_auto_visuel():
    assert needs_images("comment trouver les ruines sous-marines dans No Man's Sky") is True


def test_needs_images_non_visuel():
    assert needs_images("Explique cette erreur Python : ValueError") is False
    assert needs_images("Résous x^2 + 2x + 1 = 0") is False
    assert needs_images("ok") is False


def test_build_query_strip_trigger():
    assert build_query("Montre-moi le Nautilon") == "le Nautilon"


def test_images_markdown_galerie_et_sources():
    results = [
        ImageResult("Nautilon", "https://img.example/a.jpg", "https://site.example/a", "Wiki"),
        ImageResult("Ruines", "https://img.example/b.jpg", "https://site.example/b", "Wiki"),
    ]
    md = images_markdown(results)
    assert "[![Nautilon](https://img.example/a.jpg)](https://site.example/a)" in md
    assert "Sources images" in md
    html = render_markdown("Texte.\n" + md)
    assert 'class="image-grid"' in html
    assert "loading=" in html


def test_rendu_bloque_data_et_javascript():
    html = render_markdown("![x](javascript:alert(1))\n\n![y](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)")
    assert "javascript:" not in html
    assert "data:image" not in html


def test_search_off_ne_fait_aucun_appel(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no call")

    monkeypatch.setattr(image_search, "_search_duckduckgo", _boom)
    monkeypatch.setattr(image_search, "_search_openverse", _boom)
    monkeypatch.setattr(image_search, "_search_brave", _boom)
    with override_settings(JEJEGPT_IMAGE_SEARCH_PROVIDER="off"):
        assert image_search.search_images("Nautilon") == []
    with override_settings(JEJEGPT_IMAGE_SEARCH_PROVIDER="brave", JEJEGPT_IMAGE_SEARCH_API_KEY=""):
        # Brave sans clé : repli direct, aucun appel HTTP réel.
        assert image_search.search_images("Nautilon") == []


def test_search_fallback_second_provider(monkeypatch):
    fake = ImageResult("R", "https://img.example/r.jpg", "https://site.example/r", "S")

    def _down(*a, **k):
        raise RuntimeError("ddg down")

    monkeypatch.setattr(image_search, "_search_duckduckgo", _down)
    monkeypatch.setattr(image_search, "_search_openverse", lambda *a, **k: [fake])
    with override_settings(JEJEGPT_IMAGE_SEARCH_PROVIDER="duckduckgo"):
        assert image_search.search_images("Nautilon exotique zz") == [fake]


def test_search_ne_cache_pas_le_vide(monkeypatch):
    calls = {"n": 0}

    def _empty(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(image_search, "_search_duckduckgo", _empty)
    monkeypatch.setattr(image_search, "_search_openverse", _empty)
    with override_settings(JEJEGPT_IMAGE_SEARCH_PROVIDER="duckduckgo"):
        assert image_search.search_images("requete vide zz") == []
        assert image_search.search_images("requete vide zz") == []
    assert calls["n"] == 4  # 2 providers x 2 appels : le vide n'est jamais caché


def test_maybe_auto_images_toggle_off(db, monkeypatch):
    from chat.models import Conversation
    from chat.views import _maybe_auto_images

    conv = Conversation.objects.create(title="t", show_images=False)
    conv.messages.create(role="user", content="montre-moi le Nautilon")
    assistant = conv.messages.create(role="assistant", content="Voici.")
    called = {}

    def fake_search(q):
        called["q"] = q
        return []

    monkeypatch.setattr("chat.image_search.search_images", fake_search)
    _maybe_auto_images(conv, "montre-moi le Nautilon")
    assert called == {}
    assistant.refresh_from_db()
    assert "Images" not in assistant.content


def test_maybe_auto_images_enrichit(db, monkeypatch):
    from chat.models import Conversation
    from chat.views import _maybe_auto_images

    conv = Conversation.objects.create(title="t", show_images=True)
    conv.messages.create(role="user", content="comment trouver les ruines sous-marines")
    assistant = conv.messages.create(role="assistant", content="Voici la méthode.")
    monkeypatch.setattr(
        "chat.image_search.search_images",
        lambda q: [ImageResult("R", "https://img.example/r.jpg", "https://site.example/r", "S")],
    )
    _maybe_auto_images(conv, "comment trouver les ruines sous-marines")
    assistant.refresh_from_db()
    assert "🖼️ **Images**" in assistant.content
    assert "https://img.example/r.jpg" in assistant.content
