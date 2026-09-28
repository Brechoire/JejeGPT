import pytest
from django.test import Client
from django.urls import reverse

from chat.models import Conversation, DeepResearchRun, LLMModel, Message, Provider

pytestmark = pytest.mark.django_db

_SEQ = 0


class _InlineThread:
    """Remplace le thread : exécution synchrone pour les tests."""

    def __init__(self, target=None, args=(), **kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _provider(web=True):
    global _SEQ
    _SEQ += 1
    provider = Provider.objects.create(name=f"R{_SEQ}", base_url="http://localhost:9/v1")
    LLMModel.objects.create(
        provider=provider, model_id="m", supports_web=web,
        supports_effort=False, supports_thinking=False,
        route="responses" if web else "",
    )
    return provider


def _conversation(provider=None):
    return Conversation.objects.create(title="T", provider=provider, model_id="m")


def test_run_borne_3_cycles_puis_synthese(monkeypatch):
    from chat import providers as _pv
    from chat.research import _run_research, start_research

    calls = []
    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: calls.append(1) or "trouvaille")
    monkeypatch.setattr("chat.research.threading.Thread", _InlineThread)
    provider = _provider()
    source = _conversation(provider)
    run, error = start_research(source, "Les agents IA")
    assert error == "" and run is not None
    run.refresh_from_db()
    assert run.status == "done" and run.cycles_done == 3
    assert len(calls) == 4  # 3 cycles + synthèse
    assert Message.objects.filter(conversation=run.conversation, content="trouvaille").exists()
    assert run.conversation.title.startswith("Recherche :")
    assert run.conversation.web_search is True


def test_erreur_provider_en_cours_de_boucle(monkeypatch):
    from chat import providers as _pv
    from chat.research import _run_research

    def boom(*a, **k):
        raise _pv.ProviderError("coupure")

    monkeypatch.setattr(_pv, "chat_complete", boom)
    provider = _provider()
    source = _conversation(provider)
    run = DeepResearchRun.objects.create(subject="S", source=source, max_cycles=3)
    conv = _conversation(provider)
    run.conversation = conv
    run.save()
    assert _run_research(run.pk) is True
    run.refresh_from_db()
    assert run.status == "error" and "coupure" in run.error
    assert Message.objects.filter(conversation=conv, error="coupure").exists()


def test_sans_web_pas_de_thread(monkeypatch):
    from chat.research import start_research

    provider = _provider(web=False)
    source = _conversation(provider)
    run, error = start_research(source, "Sujet")
    assert run is None and "web" in error
    assert DeepResearchRun.objects.count() == 0


def test_double_lancement_un_seul_run(monkeypatch):
    from chat import providers as _pv
    from chat.research import start_research

    monkeypatch.setattr(_pv, "chat_complete", lambda *a, **k: "ok")
    monkeypatch.setattr("chat.research.threading.Thread", _InlineThread)
    provider = _provider()
    source = _conversation(provider)
    # Bloque la fin pour simuler un run encore actif.
    run = DeepResearchRun.objects.create(subject="S", source=source, status="running")
    run2, error = start_research(source, "Sujet")
    assert run2.pk == run.pk
    assert DeepResearchRun.objects.count() == 1


def test_start_endpoint_json_et_statut():
    provider = _provider()
    source = _conversation(provider)
    client = Client()
    run = DeepResearchRun.objects.create(subject="S", source=source, status="running", step="Cycle 1/3")
    resp = client.get(reverse("chat:research_status", args=[run.pk]))
    assert resp.json() == {
        "status": "running", "step": "Cycle 1/3", "cycles_done": 0, "max_cycles": 3,
    }
    resp = client.post(
        reverse("chat:research_start", args=[source.pk]),
        {"content": ""},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_bouton_composer_selon_support_web():
    provider = _provider(web=True)
    conv = _conversation(provider)
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert "startResearch(this)" in html
    assert "3 cycles max" in html
    provider2 = _provider(web=False)
    conv2 = _conversation(provider2)
    html2 = Client().get(f"/{conv2.pk}/").content.decode()
    assert "startResearch(this)" not in html2
    assert "Recherche approfondie indisponible" in html2


def test_banniere_progression_si_run_actif():
    provider = _provider()
    conv = _conversation(provider)
    run = DeepResearchRun.objects.create(
        subject="S", conversation=conv, status="running", step="Cycle 1/3",
    )
    html = Client().get(f"/{conv.pk}/").content.decode()
    assert 'id="research-banner"' in html
    assert f"/recherche/{run.pk}/statut/" in html


def test_synthese_exige_tableau_entite_lien():
    from chat.research import SYNTHESIS_SYSTEM

    assert "| Entité | Détail | Lien |" in SYNTHESIS_SYSTEM
    assert "noms nus sont interdits" in SYNTHESIS_SYSTEM


def test_synthese_recoit_section_urls_vues(monkeypatch):
    from chat import providers as _pv
    from chat.research import _run_research

    seen = {}
    findings = (
        "Piste : [Ashker NMS](https://youtube.com/@ashkernms) fait des lives. "
        "Voir https://nms-france.fr/."
    )

    def fake_complete(provider, model_id, messages, **kwargs):
        seen.setdefault("calls", []).append(messages)
        if len(seen["calls"]) < 4:
            return findings
        return "## Synthèse\n\nAshker NMS fait des lives."

    monkeypatch.setattr(_pv, "chat_complete", fake_complete)
    provider = _provider()
    source = _conversation(provider)
    conv = _conversation(provider)
    run = DeepResearchRun.objects.create(subject="S", source=source, max_cycles=3)
    run.conversation = conv
    run.save()
    assert _run_research(run.pk) is True
    user_msg = seen["calls"][-1][1]["content"]
    assert "URLs vues (réutilise uniquement celles-ci" in user_msg
    assert "https://youtube.com/@ashkernms" in user_msg
    assert "https://nms-france.fr/" in user_msg
    final = Message.objects.filter(conversation=conv, role="assistant").first()
    assert final is not None
    # Injection déterministe : le nom nu de la synthèse est relié.
    assert "[Ashker NMS](https://youtube.com/@ashkernms)" in final.content
    from chat.md import render_markdown

    html = render_markdown(final.content)
    assert '<a href="https://youtube.com/@ashkernms"' in html


def test_inject_seen_links_cas_limites():
    from chat.research import inject_seen_links, seen_urls

    findings = "[Ashker NMS](https://youtube.com/@ashkernms) en live."
    assert seen_urls(findings) == ["https://youtube.com/@ashkernms"]
    assert seen_urls("Voir https://a.fr/x., et https://a.fr/x") == ["https://a.fr/x"]
    # Sans URL : texte intact.
    assert inject_seen_links("Ashker fait des lives.", "Ashker fait des lives.") == "Ashker fait des lives."
    # Déjà lié : pas de double-liaison.
    linked = "[Ashker NMS](https://youtube.com/@ashkernms) en live."
    assert inject_seen_links(linked, findings) == linked
    # Code protégé.
    code = "`Ashker NMS` et ```\nAshker NMS\n``` puis Ashker NMS."
    out = inject_seen_links(code, findings)
    assert "`Ashker NMS`" in out and "```\nAshker NMS\n```" in out
    assert out.count("[Ashker NMS](https://youtube.com/@ashkernms)") == 1
    # Protocole dangereux jamais injecté.
    bad = inject_seen_links("Clique ici.", "[ici](javascript:alert(1))")
    assert bad == "Clique ici."
    assert "javascript:" not in bad
    # Repli slug depuis URL brute.
    slug = inject_seen_links("Voir nms france.", "Réf : https://nms-france.fr/.")
    assert "[nms france](https://nms-france.fr/)" in slug
