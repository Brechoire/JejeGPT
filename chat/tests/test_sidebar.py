import pytest
from django.test import Client

from chat.models import Conversation

pytestmark = pytest.mark.django_db


def test_detail_sidebar_se_replie_entierement():
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert '<aside x-show="sidebarOpen"' in html
    assert 'x-show="!sidebarOpen"' in html
    assert "Afficher le panneau" in html
    assert '<span class="w-9 flex justify-start">' in html


def test_thread_scrollbar_bord_fenetre_contenu_centre():
    from chat.models import Message

    conv = Conversation.objects.create(title="T")
    Message.objects.create(conversation=conv, role="user", content="hello")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert '<div id="thread" class="flex-1 overflow-y-auto"' in html
    assert '<div id="thread-box" class="w-full max-w-5xl mx-auto px-4 sm:px-6 py-2 space-y-8">' in html


def test_exports_presentes_en_pastilles_et_cartes():
    from chat.models import Export, Message

    conv = Conversation.objects.create(title="T")
    assistant = Message.objects.create(conversation=conv, role="assistant", content="hello")
    Export.objects.create(
        message=assistant, format="pdf", file="exports/2026/09/fake.pdf",
        file_name="Rapport.pdf", size=2048,
    )
    html = Client().get(f"/{conv.pk}/").content.decode()
    for label in ("Exporter :", ">PDF<", ">DOCX<", ">XLSX<", ">PPTX<"):
        assert label in html
    assert "Rapport.pdf" in html
    assert "Kio" in html


def test_composer_grandit_avant_scroller():
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert "max-height: 40vh" in css
    base = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")
    assert "overflowY" in base


def test_settings_sidebar_se_replie_entierement():
    html = Client().get("/parametres/").content.decode()
    assert '<aside x-show="sidebarOpen"' in html
    assert 'x-show="!sidebarOpen"' in html


def test_page_sans_theme_creme_police_systeme():
    from pathlib import Path

    from django.conf import settings

    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "bg-cream" not in html
    assert "family=Roboto" in html  # police Roboto chargée
    assert "family=Inter" not in html
    # Pile système repliée dans le CSS Tailwind précompilé (plus de config inline).
    css = (Path(settings.BASE_DIR) / "static" / "css" / "tailwind.css").read_text(encoding="utf-8")
    assert "BlinkMacSystemFont" in css
    assert 'class="bg-white text-ink font-sans h-screen"' in html


def test_sidebar_sections_et_pastille():
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "Épinglés" in html and "Projets" in html and "Chats" in html
    assert "Nouveau chat" in html
    assert 'id="sched-badge"' in html and "bg-accent" in html  # pastille conservée


def test_bulle_et_prose_style_chatgpt():
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert "#e9eef2" in css  # bulle bleu-gris
    assert "line-height: 1.5" in css  # texte 16px / 24px
    assert "line-height: 1.75" not in css
    assert "font-size: 1.625rem" in css  # h1 = 26px à 100 %
    assert "font-size: 1.25rem" in css and "font-size: 1.125rem" in css  # h2/h3
    assert "font-size: 1rem" in css  # texte normal
    assert "border-radius: 28px" in css  # composer pilule


def test_stream_live_markdown_et_shimmer():
    from pathlib import Path

    from django.conf import settings

    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "marked@18.0.14/lib/marked.umd.js" in html
    assert "dompurify@3.4.16/dist/purify.min.js" in html
    css = (Path(settings.BASE_DIR) / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert "stream-shimmer-slide" in css  # shimmer moderne
    assert "typing-caret" not in css  # ancien curseur supprimé
    assert "prefers-reduced-motion" in css  # shimmer respectueux
