import pytest
from django.test import Client
from django.urls import reverse

from chat.models import Conversation

pytestmark = pytest.mark.django_db


def _detail_html():
    conv = Conversation.objects.create(title="T")
    return Client().get(f"/{conv.pk}/").content.decode()


def test_sidebar_tiroir_overlay():
    html = _detail_html()
    assert "fixed inset-y-0 left-0 z-40" in html
    assert "md:static" in html
    assert "window.innerWidth >= 768" in html  # fermée par défaut sur mobile


def test_backdrop_fermeture_sur_les_trois_pages():
    conv = Conversation.objects.create(title="T2")
    pages = [
        Client().get(f"/{conv.pk}/").content.decode(),
        Client().get("/parametres/").content.decode(),
        Client().get(reverse("chat:scheduled_list")).content.decode(),
    ]
    for html in pages:
        assert "md:hidden" in html
        assert '@click="sidebarOpen = false"' in html
        assert "bg-black/30" in html


def test_parametres_empiles_sur_mobile():
    html = Client().get("/parametres/").content.decode()
    assert "flex-col md:flex-row" in html
    assert "overflow-x-auto" in html
    assert "whitespace-nowrap" in html
    assert "px-4 sm:px-8" in html


def test_pages_espacements_responsive():
    html = _detail_html()
    assert "px-4 sm:px-6" in html
    assert "text-3xl sm:text-[2.25rem]" in html
    assert "max-w-[calc(100vw-3rem)]" in html
    scheduled = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "px-4 sm:px-8" in scheduled


def test_tactile_coarse_pointer():
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "css" / "jejegpt.css").read_text(encoding="utf-8")
    assert "@media (pointer: coarse)" in css
    assert "padding: 0.875rem" in css
