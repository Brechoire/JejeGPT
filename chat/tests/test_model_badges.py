"""Badges de capacités du sélecteur + auto-alimentation OpenRouter."""
import pytest
from django.test import Client

from chat import providers
from chat.models import Conversation, LLMModel, Provider


def _provider(**kw):
    defaults = {"name": "OR", "base_url": "https://openrouter.ai/api/v1", "api_key": "k"}
    defaults.update(kw)
    return Provider.objects.create(**defaults)


# --- caps_from_openrouter ----------------------------------------------------


def test_caps_reasoning_detecte():
    entry = {"id": "x/y", "supported_parameters": ["temperature", "reasoning", "max_tokens"]}
    assert providers.caps_from_openrouter(entry) == {"effort": False, "web": False, "thinking": True}


def test_caps_include_reasoning_detecte():
    entry = {"id": "x/y", "supported_parameters": ["include_reasoning"]}
    assert providers.caps_from_openrouter(entry)["thinking"] is True


def test_caps_sans_raisonnement():
    entry = {"id": "x/y", "supported_parameters": ["temperature", "tools", "max_tokens"]}
    assert providers.caps_from_openrouter(entry) == {"effort": False, "web": False, "thinking": False}


@pytest.mark.parametrize("entry", [None, {}, {"id": "x"}, {"id": "x", "supported_parameters": None}, "texte"])
def test_caps_entree_minimale_repli_false(entry):
    assert providers.caps_from_openrouter(entry) == {"effort": False, "web": False, "thinking": False}


# --- model_controls : parité avec ui_controls --------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("route,caps", [
    ("responses", {"effort": True, "web": True, "thinking": True}),
    ("messages", {"effort": False, "web": False, "thinking": True}),
    ("messages", {"effort": True, "web": True, "thinking": False}),
    ("", {"effort": True, "web": True, "thinking": True}),
    ("", {"effort": False, "web": False, "thinking": False}),
])
def test_model_controls_parite_ui_controls(route, caps):
    p = _provider(base_url="http://localhost:1234/v1")
    m = LLMModel.objects.create(
        provider=p, model_id="zz-parite", route=route,
        supports_effort=caps["effort"], supports_web=caps["web"], supports_thinking=caps["thinking"],
    )
    assert providers.model_controls(m) == providers.ui_controls(p, "zz-parite")


@pytest.mark.django_db
def test_model_controls_non_verifie():
    p = _provider(base_url="http://localhost:1234/v1")
    m = LLMModel.objects.create(provider=p, model_id="zz-inconnu")
    controls = providers.model_controls(m)
    assert controls["known"] is False
    assert (controls["effort"], controls["web"], controls["thinking"]) == (False, False, False)


# --- sync_models OpenRouter ---------------------------------------------------


@pytest.mark.django_db
def test_sync_openrouter_remplit_thinking_a_la_creation(monkeypatch):
    p = _provider()
    monkeypatch.setattr(providers, "fetch_models", lambda p, timeout=30.0: ["vendor/reasoner"])
    monkeypatch.setattr(
        providers, "fetch_model_entries",
        lambda p, timeout=30.0: [{"id": "vendor/reasoner", "supported_parameters": ["reasoning"]}],
    )
    n, err = providers.sync_models(p)
    assert (n, err) == (1, "")
    m = LLMModel.objects.get(provider=p, model_id="vendor/reasoner")
    assert (m.supports_effort, m.supports_web, m.supports_thinking) == (False, False, True)


@pytest.mark.django_db
def test_sync_table_statique_prime_sur_openrouter(monkeypatch):
    p = _provider()
    monkeypatch.setattr(providers, "fetch_models", lambda p, timeout=30.0: ["kimi-k3"])
    monkeypatch.setattr(
        providers, "fetch_model_entries",
        lambda p, timeout=30.0: [{"id": "kimi-k3", "supported_parameters": ["temperature"]}],
    )
    providers.sync_models(p)
    m = LLMModel.objects.get(provider=p, model_id="kimi-k3")
    # Table : kimi-k3 = thinking-only, même si l'entrée ne signale rien.
    assert (m.supports_effort, m.supports_web, m.supports_thinking) == (False, False, True)


@pytest.mark.django_db
def test_sync_openrouter_ne_crase_pas_le_manuel(monkeypatch):
    p = _provider()
    monkeypatch.setattr(providers, "fetch_models", lambda p, timeout=30.0: ["vendor/reasoner"])
    monkeypatch.setattr(
        providers, "fetch_model_entries",
        lambda p, timeout=30.0: [{"id": "vendor/reasoner", "supported_parameters": ["reasoning"]}],
    )
    providers.sync_models(p)
    m = LLMModel.objects.get(provider=p, model_id="vendor/reasoner")
    m.supports_web = True
    m.supports_thinking = False
    m.save(update_fields=["supports_web", "supports_thinking"])
    providers.sync_models(p)  # re-sync : l'override manuel survit
    m.refresh_from_db()
    assert (m.supports_web, m.supports_thinking) == (True, False)


@pytest.mark.django_db
def test_sync_non_openrouter_pas_de_second_get(monkeypatch):
    p = _provider(base_url="http://localhost:1234/v1")
    monkeypatch.setattr(providers, "fetch_models", lambda p, timeout=30.0: ["local-model"])

    def _boom(p, timeout=30.0):
        raise AssertionError("fetch_model_entries ne doit pas être appelé hors OpenRouter")

    monkeypatch.setattr(providers, "fetch_model_entries", _boom)
    n, err = providers.sync_models(p)
    assert (n, err) == (1, "")


# --- rendu picker -------------------------------------------------------------


@pytest.mark.django_db
def test_picker_affiche_badges_et_non_verifie():
    p = _provider(base_url="http://localhost:1234/v1")
    LLMModel.objects.create(
        provider=p, model_id="bon-modele", route="responses",
        supports_effort=True, supports_web=True, supports_thinking=True,
    )
    LLMModel.objects.create(
        provider=p, model_id="penseur", route="messages", supports_thinking=True,
    )
    LLMModel.objects.create(provider=p, model_id="zz-inconnu")
    c = Conversation.objects.create(title="T", provider=p, model_id="bon-modele")
    html = Client().get(f"/{c.pk}/").content.decode()
    assert "Internet" in html  # web (route responses)
    assert "Effort" in html  # effort (route responses)
    assert "/responses" in html  # pastille route
    assert "Réflexion" in html  # thinking (route messages)
    assert "non vérifié" in html  # modèle inconnu
    assert "Paramètres → Providers" in html
