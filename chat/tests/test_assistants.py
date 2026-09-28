import pytest
from django.test import Client
from django.urls import reverse

from chat.models import AppSettings, Assistant, Conversation, LLMModel, Provider

pytestmark = pytest.mark.django_db

_SEQ = 0


def _provider():
    global _SEQ
    _SEQ += 1
    return Provider.objects.create(name=f"PA{_SEQ}", base_url="http://localhost:9/v1")


def _default_llm():
    provider = _provider()
    LLMModel.objects.create(provider=provider, model_id="def-m", display_name="def-m")
    solo = AppSettings.get_solo()
    solo.default_provider = provider
    solo.default_model_id = "def-m"
    solo.save()
    return provider


def _assistant(**kwargs):
    global _SEQ
    _SEQ += 1
    data = {
        "name": f"Assistant {_SEQ}",
        "description": "Desc",
        "instructions": "Réponds en français.",
        "temperature": 0.5,
        "starters": "Relis ce texte :\nRésume en 5 points :",
    }
    data.update(kwargs)
    return Assistant.objects.create(**data)


# ---------------- Modèle ----------------

def test_starter_list_nettoie_et_tronque():
    a = Assistant(name="X", starters="  Un  \n\nDeux\nTrois\nQuatre\nCinq\n")
    assert a.starter_list() == ["Un", "Deux", "Trois", "Quatre"]
    assert Assistant.normalize_starters(" a \n\n b \n") == "a\nb"


def test_starter_list_vide():
    assert Assistant(name="X").starter_list() == []


# ---------------- CRUD ----------------

def test_create_ok_et_doublon():
    client = Client()
    avant = Assistant.objects.count()  # le seed « Prompt Suno » peut déjà exister
    resp = client.post(reverse("chat:assistant_create"), {
        "name": "Relecteur", "description": "Relit", "instructions": "Sois concis.",
        "temperature": "0.3", "reasoning_effort": "medium",
        "web_search": "1", "thinking_enabled": "on",
        "starters": "Relis :\nRésume :",
    })
    assistant = Assistant.objects.get(name="Relecteur")
    assert resp.status_code == 302
    assert assistant.temperature == 0.3
    assert assistant.reasoning_effort == "medium"
    assert assistant.web_search is True
    assert assistant.starter_list() == ["Relis :", "Résume :"]
    # Doublon refusé.
    client.post(reverse("chat:assistant_create"), {"name": "Relecteur"})
    assert Assistant.objects.filter(name="Relecteur").count() == 1
    # Nom manquant refusé.
    client.post(reverse("chat:assistant_create"), {"name": "  "})
    assert Assistant.objects.count() == avant + 1


def test_update_borne_temperature_et_effort_inconnu():
    a = _assistant()
    Client().post(reverse("chat:assistant_update", args=[a.pk]), {
        "name": "Relecteur+", "description": "", "instructions": "Base.",
        "temperature": "9", "reasoning_effort": "nope",
        "starters": "1\n2\n3\n4\n5\n6",
    })
    a.refresh_from_db()
    assert a.name == "Relecteur+"
    assert a.temperature == 2.0
    assert a.reasoning_effort == ""
    assert a.starter_list() == ["1", "2", "3", "4"]
    # thinking décoché explicitement.
    Client().post(reverse("chat:assistant_update", args=[a.pk]), {
        "name": "Relecteur+", "temperature": "0.7",
        "thinking_enabled": "0", "starters": "",
    })
    a.refresh_from_db()
    assert a.thinking_enabled is False


def test_list_et_detail_affichent():
    a = _assistant(name="Alpha", description="Aide FR")
    html = Client().get(reverse("chat:assistant_list")).content.decode()
    assert "Assistants" in html and "Alpha" in html and "Nouvel assistant" in html
    # Nouvelle présentation : grille de cartes + formulaire replié + filtre.
    assert 'id="assistant-grid"' in html and 'x-show="showCreate"' in html
    assert 'id="assistant-search"' in html and "filterAssistants" in html
    html2 = Client().get(reverse("chat:assistant_detail", args=[a.pk])).content.decode()
    assert "Réponds en français." in html2
    assert "Relis ce texte" in html2  # aperçu starters
    assert "Zone danger" in html2 and "Dupliquer" in html2


def test_duplicate_nom_unique():
    a = _assistant(name="Base")
    client = Client()
    client.post(reverse("chat:assistant_duplicate", args=[a.pk]))
    copy = Assistant.objects.get(name="Base (copie)")
    assert copy.instructions == a.instructions
    assert copy.starter_list() == a.starter_list()
    client.post(reverse("chat:assistant_duplicate", args=[a.pk]))
    assert Assistant.objects.filter(name="Base (copie) 2").exists()


def test_delete_conserve_conversations():
    provider = _default_llm()
    a = _assistant()
    conv = Conversation.objects.create(
        title="T", provider=provider, model_id="def-m", assistant=a,
    )
    Client().post(reverse("chat:assistant_delete", args=[a.pk]))
    assert not Assistant.objects.filter(pk=a.pk).exists()
    conv.refresh_from_db()
    assert conv.assistant_id is None  # SET_NULL : conversation conservée


# ---------------- Démarrage conversation ----------------

def test_start_conversation_copie_config_et_modele_defaut():
    _default_llm()
    a = _assistant(temperature=0.2, reasoning_effort="high", web_search=True)
    resp = Client().post(reverse("chat:assistant_start_conversation", args=[a.pk]))
    conv = Conversation.objects.get()
    assert resp.url == conv.get_absolute_url()
    assert conv.assistant_id == a.pk
    assert conv.title == a.name
    assert conv.system_prompt == "Réponds en français."
    assert conv.temperature == 0.2
    assert conv.reasoning_effort == "high"
    assert conv.web_search is True
    assert conv.model_id == "def-m"  # modèle par défaut, pas de modèle par assistant


def test_start_conversation_sans_instructions_reprend_preset():
    from django.conf import settings

    _default_llm()
    a = _assistant(instructions="")
    Client().post(reverse("chat:assistant_start_conversation", args=[a.pk]))
    conv = Conversation.objects.get()
    assert conv.system_prompt == settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT


def test_chat_hero_affiche_starters_et_badge():
    _default_llm()
    a = _assistant()
    Client().post(reverse("chat:assistant_start_conversation", args=[a.pk]))
    conv = Conversation.objects.get()
    html = Client().get(conv.get_absolute_url()).content.decode()
    assert f"🤖 {a.name}" in html
    assert "Relis ce texte" in html  # bouton starter remplit le composer


def test_seed_prompt_suno_fonctionnel():
    a = Assistant.objects.get(name="Prompt Suno")
    assert a.temperature == 0.8
    assert a.starter_list()[0].startswith("Je veux une chanson sans guitare")
    for marqueur in ("Exclude", "[Accordion Solo]", "Style"):
        assert marqueur in a.instructions


def test_seed_vague1_6_assistants():
    attendus = [
        "Correcteur FR express", "Synthétiseur", "Rédacteur d'emails pro",
        "Traducteur nuancé", "Veilleur web", "Brainstormeur d'idées",
    ]
    for nom in attendus:
        a = Assistant.objects.get(name=nom)
        assert a.instructions.strip() != ""
        assert a.starter_list() != []
    veilleur = Assistant.objects.get(name="Veilleur web")
    assert veilleur.web_search is True


def test_sidebar_liste_assistants():
    _assistant(name="Zeta")
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    # Section sidebar supprimée (doublon avec l'onglet) : accès via l'onglet.
    assert "Tous les assistants" not in html
    assert 'href="/assistants/"' in html
    html2 = Client().get(reverse("chat:assistant_list")).content.decode()
    assert "Zeta" in html2


def test_sidebar_onglets_chat_assistants_sans_work_code():
    conv = Conversation.objects.create(title="T")
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert ">Assistants</a>" in html
    assert ">Work</button>" not in html and ">Code</button>" not in html
    assert 'href="/assistants/"' in html
    # Onglet actif côté chat.
    assert '<a href="/" class="flex-1 py-1.5 text-center active" aria-current="page">Chat</a>' in html
    # Onglet actif côté assistants.
    html2 = Client().get(reverse("chat:assistant_list")).content.decode()
    assert '<a href="/assistants/" class="flex-1 py-1.5 text-center active" aria-current="page">Assistants</a>' in html2
