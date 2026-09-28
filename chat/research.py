"""Recherche approfondie bornée vers une conversation dédiée."""
from __future__ import annotations

import logging
import re
import threading

from django.conf import settings
from django.db import close_old_connections

from .scheduler.engine import LINK_RULE, SOURCES_SUFFIX

logger = logging.getLogger(__name__)

RESEARCH_MAX_CYCLES = 3

CYCLE_SYSTEM = (
    "Tu es un assistant de recherche web. À partir du sujet et des découvertes "
    "déjà faites, fournis en Markdown concis : 1) les points clés vérifiés avec "
    "leurs sources (liens directs), 2) de nouvelles requêtes à explorer ensuite. "
    + LINK_RULE
)

SYNTHESIS_SYSTEM = (
    "Tu rédiges un rapport de recherche approfondie en Markdown français structuré "
    "(titres, listes, tableaux si pertinent, exemples concrets). "
    "Toute entité reprise des Découvertes s'écrit [Nom exact](URL) avec l'URL telle "
    "qu'elle figure dans les Découvertes ; quand tu compares ou classes des entités "
    "(chaînes, outils, lieux…), utilise un tableau | Entité | Détail | Lien |. "
    "Les noms nus sont interdits si leur URL figure dans les Découvertes. "
    + SOURCES_SUFFIX + " " + LINK_RULE
)

_RE_SEEN_URL = re.compile(r"https?://[^\s<>\]\)]+")
_RE_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_RE_FENCED = re.compile(r"```.*?```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")


def seen_urls(findings_text: str) -> list[str]:
    """URL vues dans les découvertes, dédupliquées, ordre conservé."""
    seen: list[str] = []
    for url in _RE_SEEN_URL.findall(findings_text or ""):
        url = url.rstrip(".,;:")
        if url not in seen:
            seen.append(url)
    return seen


def inject_seen_links(synthesis_md: str, findings_text: str) -> str:
    """Relie les noms nus de la synthèse aux URL vues dans les découvertes.

    Construit nom→URL depuis les couples [nom](url) des découvertes (repli :
    nom = dernier segment lisible de l'URL), puis remplace les occurrences nues
    par [Nom](URL). Idempotent, ignore le code (inline/blocs) et les liens
    existants, n'invente jamais d'URL (zéro correspondance → texte intact).
    """
    if not synthesis_md or not findings_text:
        return synthesis_md
    pairs: dict[str, tuple[str, str]] = {}
    for name, url in _RE_MD_LINK.findall(findings_text):
        label = name.strip()
        if label and label.lower() not in pairs and url.startswith(("http://", "https://")):
            pairs[label.lower()] = (label, url)
    for url in seen_urls(findings_text):
        segment = url.rstrip("/").rsplit("/", 1)[-1]
        segment = re.sub(r"\.[a-z]{2,6}$", "", segment)  # nom de domaine nu → sans TLD
        slug = segment.replace("-", " ").replace("_", " ").strip()
        if slug and slug.lower() not in pairs and len(slug) >= 3:
            pairs[slug.lower()] = (slug, url)
    if not pairs:
        return synthesis_md
    # Protège code (blocs + inline) et liens existants via placeholders.
    saved: list[str] = []

    def _save(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    protected = _RE_FENCED.sub(_save, synthesis_md)
    protected = _RE_INLINE_CODE.sub(_save, protected)
    protected = re.sub(r"\[[^\]\n]+\]\([^)\s]+\)", _save, protected)
    # Noms longs d'abord (évite de lier un préfixe d'un nom composé).
    for _key, (label, url) in sorted(pairs.items(), key=lambda kv: -len(kv[1][0])):
        protected = re.sub(
            r"(?<![\w\]/])" + re.escape(label) + r"(?![\w(])",
            f"[{label}]({url})",
            protected,
            count=0,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], protected)


def active_run_for(source_conv):
    """Run en cours pour une conversation source, None sinon."""
    from .models import DeepResearchRun

    return (
        DeepResearchRun.objects.filter(source=source_conv, status="running").first()
    )


def web_supported(provider, model_id: str) -> bool:
    """Recherche web réellement envoyable (règle unique : voir ui_controls)."""
    from . import providers

    try:
        return bool(providers.ui_controls(provider, model_id).get("web"))
    except Exception:
        return False


def start_research(source_conv, subject: str):
    """Crée conversation dédiée + run et lance le worker. Retourne (run, erreur)."""
    from .models import Conversation, DeepResearchRun, Message

    subject = (subject or "").strip()[:300]
    if not subject:
        return None, "Sujet vide."
    if source_conv.provider is None or not source_conv.model_id:
        return None, "Aucun provider/modèle sélectionné."
    if not web_supported(source_conv.provider, source_conv.model_id):
        return None, "La recherche web n'est pas supportée par ce modèle."
    existing = active_run_for(source_conv)
    if existing is not None:
        return existing, ""
    conv = Conversation.objects.create(
        title=f"Recherche : {subject[:60]}",
        provider=source_conv.provider,
        model_id=source_conv.model_id,
        system_prompt=settings.JEJEGPT_DEFAULT_SYSTEM_PROMPT,
        temperature=settings.JEJEGPT_DEFAULT_TEMPERATURE,
        web_search=True,
    )
    Message.objects.create(conversation=conv, role="user", content=subject)
    run = DeepResearchRun.objects.create(
        subject=subject, conversation=conv, source=source_conv,
        max_cycles=RESEARCH_MAX_CYCLES,
    )
    thread = threading.Thread(
        target=_thread_main, args=(run.pk,),
        name=f"jejegpt-research-{run.pk}", daemon=True,
    )
    thread.start()
    return run, ""


def _set_run(run, **fields) -> None:
    for key, value in fields.items():
        setattr(run, key, value)
    run.save(update_fields=list(fields))


def _record(conv, provider, model_id: str, usage: dict, success: bool, error: str = "") -> None:
    from . import providers
    from .models import UsageRecord

    try:
        UsageRecord.objects.create(
            conversation=conv,
            provider=provider,
            model_id=model_id,
            route=providers.resolve_route(provider, model_id),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            duration_ms=int(usage.get("duration_ms") or 0),
            success=success,
            error=(error or "")[:300],
        )
    except Exception:
        logger.exception("research usage persist failed")


def _complete(provider, model_id: str, messages: list[dict], conv, usage: dict, tag: str) -> str:
    from . import providers

    effort = conv.reasoning_effort if conv.reasoning_effort in providers.EFFORT_LEVELS else ""
    return providers.chat_complete(
        provider, model_id, messages,
        temperature=conv.temperature,
        timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
        session_id=f"research-{tag}",
        route=providers.resolve_route(provider, model_id),
        options={"effort": effort, "web": True, "thinking": bool(conv.thinking_enabled)},
        usage_collector=usage,
    )


def _thread_main(run_pk: int) -> None:
    try:
        close_old_connections()
        _run_research(run_pk)
    except Exception:
        logger.exception("research thread %s crashed", run_pk)
    finally:
        close_old_connections()


def _run_research(run_pk: int) -> bool:
    """Boucle bornée : cycles de recherche puis synthèse. False si run introuvable."""
    from . import providers
    from .models import DeepResearchRun, Message

    try:
        run = DeepResearchRun.objects.select_related("conversation").get(pk=run_pk)
    except DeepResearchRun.DoesNotExist:
        return False
    conv = run.conversation
    if conv is None:
        _set_run(run, status="error", error="Conversation dédiée introuvable.", step="")
        return True
    provider, model_id = conv.provider, conv.model_id
    findings: list[str] = []
    try:
        for cycle in range(1, run.max_cycles + 1):
            _set_run(run, step=f"Cycle {cycle}/{run.max_cycles} : recherche…", cycles_done=cycle - 1)
            usage = providers._empty_usage()
            prior = "\n".join(findings) if findings else "(aucune)"
            reply = _complete(
                provider, model_id,
                [
                    {"role": "system", "content": CYCLE_SYSTEM},
                    {"role": "user", "content": f"Sujet : {run.subject}\nDécouvertes précédentes :\n{prior}"},
                ],
                conv, usage, f"{run.pk}-c{cycle}",
            )
            _record(conv, provider, model_id, usage, success=True)
            if reply.strip():
                findings.append(reply.strip())
            _set_run(run, cycles_done=cycle)
        _set_run(run, step="Synthèse du rapport…")
        usage = providers._empty_usage()
        joined = "\n".join(findings)
        urls = seen_urls(joined)
        urls_section = (
            "URLs vues (réutilise uniquement celles-ci, aucune autre) :\n"
            + ("\n".join(f"- {url}" for url in urls) if urls else "(aucune)")
        )
        final = _complete(
            provider, model_id,
            [
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user", "content": f"Sujet : {run.subject}\nDécouvertes :\n{joined}\n\n{urls_section}"},
            ],
            conv, usage, f"{run.pk}-final",
        )
        _record(conv, provider, model_id, usage, success=True)
        final = inject_seen_links(final, joined)
        Message.objects.create(conversation=conv, role="assistant", content=final)
        conv.save()
        _set_run(run, status="done", step="", cycles_done=run.max_cycles)
    except providers.ProviderError as exc:
        usage = providers._empty_usage()
        _record(conv, provider, model_id, usage, success=False, error=exc.message)
        Message.objects.create(conversation=conv, role="assistant", content="", error=exc.message)
        conv.save()
        _set_run(run, status="error", error=exc.message[:500], step="")
    except Exception as exc:
        logger.exception("research run %s crashed", run.pk)
        Message.objects.create(
            conversation=conv, role="assistant", content="",
            error=f"Recherche interrompue : {exc}"[:500],
        )
        conv.save()
        _set_run(run, status="error", error=str(exc)[:500], step="")
    return True
