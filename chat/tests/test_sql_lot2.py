"""Lot 2 SQL : sidebar bornée, recherche bornée, defer clé, index, purge usage, sync bulk.

Chaque test verrouille le contrat préservé ET le gain.
"""
import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone

from chat.models import Conversation, Project, Provider, UsageRecord

pytestmark = pytest.mark.django_db


def test_sidebar_bornee_et_colonnes_suffisantes():
    p = Project.objects.create(name="P", instructions="I")
    for i in range(3):
        Conversation.objects.create(title=f"C{i}", project=p)
    html = Client().get(f"/{Conversation.objects.first().pk}/").content.decode()
    # Rendu inchangé : titres + nom projet présents.
    assert "C0" in html and "C2" in html and "P" in html


def test_sidebar_ne_charge_pas_textes_lourds():
    # La page detail charge légitimement la conversation complète (composer...) :
    # on vérifie ici le seul périmètre optimisé, _sidebar_ctx.
    from chat.views import _sidebar_ctx

    Conversation.objects.create(title="T", system_prompt="X" * 5000)
    conv = Conversation.objects.first()
    with CaptureQueriesContext(connection) as ctx:
        _sidebar_ctx(conv)
    assert ctx.captured_queries
    for q in ctx.captured_queries:
        assert "system_prompt" not in q["sql"].lower()


def test_sidebar_bornee_50():
    for i in range(55):
        Conversation.objects.create(title=f"C{i:03d}")
    from chat.views import _sidebar_ctx

    ctx = _sidebar_ctx(Conversation.objects.first())
    total = len(ctx["recent_convs"]) + len(ctx["older_convs"])
    assert total == 50  # borne 50+50... ici tout est récent donc 50 récents


def test_global_search_borne_q():
    c = Conversation.objects.create(title="T")
    c.messages.create(role="user", content="bonjour")
    # Requête géante : bornée à 80, pas d'erreur, comportement identique.
    resp = Client().get("/recherche/?q=" + "a" * 500)
    assert resp.status_code == 200
    resp = Client().get("/recherche/?q=bonjour")
    assert "bonjour" in resp.content.decode()


def test_provider_lists_sans_cle_api():
    Provider.objects.create(name="P1", base_url="http://x/v1", api_key="SECRET")
    conv = Conversation.objects.create(title="T")
    with CaptureQueriesContext(connection) as ctx:
        Client().get(f"/{conv.pk}/")
    for q in ctx.captured_queries:
        assert "SECRET" not in q["sql"]
        # La colonne api_key ne doit pas être lue en liste (defer).
        assert "api_key" not in q["sql"].lower()


def test_settings_providers_affiche_pastille_cle():
    Provider.objects.create(name="P1", base_url="http://x/v1", api_key="SECRET")
    html = Client().get("/parametres/providers/").content.decode()
    # Le seul endroit qui lit la clé : pastille configurée/absente, conservée.
    assert "configurée" in html


def test_index_socle_crees():
    with connection.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN "
                    "('chat_message','chat_conversation','chat_usagerecord','chat_scheduledtask',"
                    "'chat_scheduledrun','chat_export','chat_memoryfact','chat_deepresearchrun',"
                    "'chat_llmmodel')")
        names = {r[0] for r in cur.fetchall()}
    for expected in ("msg_conv_created_id", "conv_updated", "usage_created",
                     "task_enabled_next", "run_task_created", "exp_created",
                     "mem_active_created", "drr_source_status", "llm_provider_model"):
        assert expected in names


def test_explain_utilise_index():
    with connection.cursor() as cur:
        cur.execute("EXPLAIN QUERY PLAN SELECT * FROM chat_message "
                    "WHERE conversation_id = 1 ORDER BY created_at, id")
        plan = " ".join(r[3] for r in cur.fetchall())
    assert "USING INDEX" in plan and "SCAN" not in plan.replace("USING", "")


def test_purge_usage_dry_run_puis_apply(settings):
    from django.core.management import call_command

    settings.JEJEGPT_USAGE_RETENTION_DAYS = 30
    conv = Conversation.objects.create(title="C")
    vieux = UsageRecord.objects.create(conversation=conv, model_id="m")
    recent = UsageRecord.objects.create(conversation=conv, model_id="m")
    UsageRecord.objects.filter(pk=vieux.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=60))
    call_command("purge_usage")
    assert UsageRecord.objects.filter(pk=vieux.pk).exists()
    call_command("purge_usage", "--apply")
    assert not UsageRecord.objects.filter(pk=vieux.pk).exists()
    assert UsageRecord.objects.filter(pk=recent.pk).exists()


def test_sync_bulk_preserve_overrides_et_obsolètes(monkeypatch):
    from chat import providers

    p = Provider.objects.get(name="OpenCode Go")
    monkeypatch.setattr(providers, "fetch_models",
                        lambda prov, timeout=30.0: ["m-new-1", "m-new-2", "m-new-3"])
    n, err = providers.sync_models(p)
    assert (n, err) == (3, "")
    assert set(p.cached_models.values_list("model_id", flat=True)) == {"m-new-1", "m-new-2", "m-new-3"}
    # Override manuel puis re-sync : survit, obsolète supprimé.
    m = p.cached_models.get(model_id="m-new-1")
    m.supports_web = True
    m.save(update_fields=["supports_web"])
    monkeypatch.setattr(providers, "fetch_models",
                        lambda prov, timeout=30.0: ["m-new-1", "m-new-4"])
    providers.sync_models(p)
    assert p.cached_models.get(model_id="m-new-1").supports_web is True
    assert not p.cached_models.filter(model_id="m-new-2").exists()
    assert p.cached_models.filter(model_id="m-new-4").exists()


@override_settings(JEJEGPT_USAGE_RETENTION_DAYS=30)
def test_purge_usage_ne_touche_pas_periode_affichee():
    from django.core.management import call_command

    conv = Conversation.objects.create(title="C")
    UsageRecord.objects.create(conversation=conv, model_id="m")
    call_command("purge_usage", "--apply")
    assert UsageRecord.objects.count() == 1
