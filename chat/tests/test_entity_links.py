"""Entités nommées cliquables : consigne présente dans les prompts, sans URL inventée."""
from pathlib import Path

from django.conf import settings


def test_prompt_defaut_exige_liens_vus_uniquement():
    prompt = settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT
    assert "[Nom exact](https://URL officielle)" in prompt
    assert "n'invente jamais d'URL" in prompt


def test_prompts_recherche_alignes():
    from chat.research import CYCLE_SYSTEM, SYNTHESIS_SYSTEM
    from chat.scheduler.engine import LINK_RULE, SOURCES_SUFFIX

    assert LINK_RULE in CYCLE_SYSTEM
    assert LINK_RULE in SYNTHESIS_SYSTEM
    assert SOURCES_SUFFIX in SYNTHESIS_SYSTEM
    assert "n'invente jamais d'URL" in LINK_RULE


def test_infobulle_globe_mentionne_liens():
    tpl = (Path(settings.BASE_DIR) / "templates" / "chat" / "partials" / "composer_options.html").read_text(encoding="utf-8")
    assert "liens cliquables sur les noms" in tpl
