import pytest
from django.test import Client
from django.urls import reverse

from chat.models import TaskTemplate, template_variables

pytestmark = pytest.mark.django_db


def test_variables_extraction_dedupe_ordre():
    assert template_variables("{{sujet}} et {{nombre}} puis {{sujet}}") == ["sujet", "nombre"]
    assert template_variables("sans variable") == []
    assert template_variables("{{  sujet  }}") == ["sujet"]
    assert template_variables("{{in-valide}} {{ok}}") == ["ok"]


def test_render_substitution_stricte():
    tpl = TaskTemplate(name="T", body="Sujet : {{sujet}}, top {{nombre}}.")
    assert tpl.render({"sujet": "K-pop", "nombre": "10"}) == "Sujet : K-pop, top 10."
    with pytest.raises(ValueError):
        tpl.render({"sujet": "K-pop"})
    with pytest.raises(ValueError):
        tpl.render({"sujet": "K-pop", "nombre": "  "})


def test_seed_recherche_approfondie():
    tpl = TaskTemplate.objects.get(name="Recherche approfondie")
    assert tpl.variables() == ["sujet", "nombre"]
    assert tpl.suggest_web is True
    assert tpl.render({"sujet": "IA", "nombre": "5"}).count("{{") == 0


def test_crud_gabarits():
    client = Client()
    response = client.post(reverse("chat:template_create"), {
        "name": "Brief", "description": "Court", "body": "Résume {{sujet}} en {{nombre}} points.",
    })
    assert response.status_code == 302
    tpl = TaskTemplate.objects.get(name="Brief")
    assert tpl.variables() == ["sujet", "nombre"]
    # Refusé : body sans variable.
    response = client.post(reverse("chat:template_create"), {
        "name": "Vide", "body": "Aucune variable ici.",
    })
    assert response.status_code == 302
    assert not TaskTemplate.objects.filter(name="Vide").exists()
    # Refusé : doublon de nom.
    response = client.post(reverse("chat:template_create"), {
        "name": "Brief", "body": "Autre {{sujet}}.",
    })
    assert TaskTemplate.objects.filter(name="Brief").count() == 1
    # Modification.
    client.post(reverse("chat:template_update", args=[tpl.pk]), {
        "name": "Brief+", "body": "Synthèse {{sujet}}.", "suggest_web": "1",
    })
    tpl.refresh_from_db()
    assert tpl.name == "Brief+" and tpl.suggest_web is True
    # Suppression.
    client.post(reverse("chat:template_delete", args=[tpl.pk]))
    assert not TaskTemplate.objects.filter(pk=tpl.pk).exists()


def test_liste_affiche_gabarits_et_assistant():
    html = Client().get(reverse("chat:scheduled_list")).content.decode()
    assert "Gabarits" in html and "Recherche approfondie" in html
    assert "sujet" in html and "nombre" in html
    assert 'id="sched-tpl-select"' in html
    assert 'id="sched-tpl-vars"' in html and 'id="sched-tpl-preview"' in html
    assert 'id="sched-tpl-use"' in html and 'id="sched-templates"' in html
    assert "Nouveau gabarit" in html


def test_create_refuse_variables_non_substituees():
    from chat.models import Conversation, Provider, ScheduledTask

    provider = Provider.objects.create(name="Pvar", base_url="http://localhost:9/v1")
    response = Client().post(reverse("chat:scheduled_create"), {
        "name": "X", "prompt": "Résume {{sujet}}.", "frequency": "daily",
        "time": "08:30", "provider": str(provider.pk), "model_id": "m",
        "enabled": "1",
    })
    assert response.status_code == 302
    assert ScheduledTask.objects.count() == 0
    assert Conversation.objects.count() == 0
