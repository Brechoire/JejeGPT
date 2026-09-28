import time
import xml.etree.ElementTree as ET

import pytest
from django.test import Client
from django.urls import reverse

from chat.md import render_markdown
from chat.models import Conversation
from chat.scores import (
    abc_to_events,
    abc_to_musicxml,
    detect_score_request,
    extract_abc_blocks,
    score_extra_message,
    score_system_instruction,
)

pytestmark = pytest.mark.django_db

FRERE_JACQUES = """X:1
T:Frère Jacques
M:4/4
L:1/4
Q:1/4=100
K:C
C D E C|C D E C|E F G2|E F G2|G A G F E C|G A G F E C|C G, C2|C G, C2|]"""


# ---------------- Détection ----------------

def test_partition_force_et_parse():
    det = detect_score_request("/partition Frère Jacques en Do à 60 BPM")
    assert det["source"] == "command"
    assert det["key"] == "C" and det["tempo"] == 60
    assert "Frère Jacques" in det["title"]


def test_sans_partition_veto():
    assert detect_score_request("/sans-partition joue Frère Jacques") is None


def test_vetos_non_musicaux():
    for texte in (
        "Comment partitionner mon disque dur ?",
        "Prends des notes de la réunion",
        "Note de frais de mars",
        "Il joue un rôle important",
        "Gamme de prix des abonnements",
        "Corrige mes fautes c'est pour demain",
        "Qui est Fussoir ? Il fait quoi ?",
        "Je veux une chanson sans guitare",  # Suno ≠ partition
        "d'accord, on fait comme ça",
        "",
    ):
        assert detect_score_request(texte) is None, texte


def test_positifs_musicaux():
    assert detect_score_request("Affiche Frère Jacques en Do, 4/4, 60 BPM") is not None
    assert detect_score_request("Donne-moi la partition de cette mélodie") is not None
    assert detect_score_request("Donne-moi les accords au piano") is not None
    assert detect_score_request("Joue-moi quelque chose au piano") is not None
    assert detect_score_request("Montre la gamme de Do majeur") is not None


def test_instruction_et_extra():
    det = detect_score_request("/partition Frère Jacques en Do")
    texte = score_system_instruction(det)
    assert "```abc" in texte and "Frère Jacques" in texte
    assert score_extra_message("Bonjour") is None
    extra = score_extra_message("/partition Test")
    assert extra["role"] == "system" and "```abc" in extra["content"]


# ---------------- Blocs ABC ----------------

def test_extract_blocs():
    md = "Voici :\n```abc\nK:C\nC D E|\n```\nFin."
    assert extract_abc_blocks(md) == ["K:C\nC D E|"]
    assert extract_abc_blocks("pas de bloc") == []


def test_markdown_garde_classe_abc():
    html = render_markdown("```abc\nK:C\nC D E|\n```")
    assert "language-abc" in html


# ---------------- Events / MusicXML ----------------

def test_events_frere_jacques():
    data = abc_to_events(FRERE_JACQUES)
    assert data["title"] == "Frère Jacques" and data["tempo_bpm"] == 100
    premiers = [(e["midi"], e["start"], e["dur"]) for e in data["events"][:4]]
    assert premiers == [(60, 0.0, 1.0), (62, 1.0, 1.0), (64, 2.0, 1.0), (60, 3.0, 1.0)]
    assert data["events"][0]["name"] == "Do"
    assert len(data["events"]) == 32


def test_musicxml_valide_frere_jacques():
    xml = abc_to_musicxml(FRERE_JACQUES)
    body = "\n".join(line for line in xml.splitlines() if not line.startswith("<!DOCTYPE"))
    root = ET.fromstring(body)
    mesures = root.find("part").findall("measure")
    assert len(mesures) == 8
    premiere = mesures[0].find("note").find("pitch")
    assert (premiere.find("step").text, premiere.find("octave").text) == ("C", "4")


def test_erreurs_claires():
    with pytest.raises(ValueError, match="Bloc ABC vide"):
        abc_to_events("   ")
    with pytest.raises(ValueError, match="Aucune note"):
        abc_to_events("X:1\nT:Rien\nK:C\n")
    with pytest.raises(ValueError, match="Tuplets"):
        abc_to_events("K:C\n(3CDE|")
    with pytest.raises(ValueError, match="brisés"):
        abc_to_events("K:C\nC>D|")
    with pytest.raises(ValueError, match="illisible"):
        abc_to_events("K:C\nyolo|")


def test_perf_64_mesures():
    gros = "X:1\nT:Gros\nM:4/4\nL:1/4\nK:C\n" + "C D E F|" * 64
    debut = time.perf_counter()
    data = abc_to_events(gros)
    assert time.perf_counter() - debut < 1.0
    assert len(data["events"]) == 256


# ---------------- Vues ----------------

def test_history_injecte_sans_modifier_le_message():
    conv = Conversation.objects.create(title="T")
    from chat.models import Message

    Message.objects.create(conversation=conv, role="user", content="/partition Frère Jacques en Do")
    from chat.views import _history

    hist = _history(conv)
    assert len(hist) == 2
    assert hist[0] == {"role": "user", "content": "/partition Frère Jacques en Do"}
    assert hist[1]["role"] == "system" and "```abc" in hist[1]["content"]


def test_history_sans_injection_hors_musique():
    conv = Conversation.objects.create(title="T")
    from chat.models import Message

    Message.objects.create(conversation=conv, role="user", content="Corrige mes fautes")
    from chat.views import _history

    assert _history(conv) == [{"role": "user", "content": "Corrige mes fautes"}]


def test_endpoints_events_et_musicxml():
    client = Client()
    resp = client.post(reverse("chat:score_events"), {"abc": FRERE_JACQUES})
    assert resp.status_code == 200
    assert resp.json()["events"][0]["midi"] == 60
    resp2 = client.post(reverse("chat:score_events"), {"abc": "n'importe quoi"})
    assert resp2.status_code == 400
    resp3 = client.post(reverse("chat:score_musicxml"), {"abc": FRERE_JACQUES})
    assert resp3.status_code == 200
    assert "attachment" in resp3["Content-Disposition"]


def test_demo_200_avec_3_exemples():
    html = Client().get(reverse("chat:score_demo")).content.decode()
    assert "Frère Jacques" in html and "Joyeux anniversaire" in html and "Gamme de Do" in html
    assert html.count("language-abc") == 3
