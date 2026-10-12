"""Test web empirique d'un modèle : prouve la navigation au lieu de la déclarer.

Bouton « Tester » par ligne modèle (onglet Providers) : envoie une sonde datée
avec web forcé, analyse la réponse (refus ? liens ? récence ?), puis vérifie
côté serveur que les URL citées existent (anti-hallucination).

Le test ne modifie JAMAIS supports_web : seul le verdict (statut + date) est
persisté, l'utilisateur garde la main sur les cases.
"""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Phrases de refus explicite (insensible à la casse, accents variés).
_REFUSALS = (
    "ne peux pas naviguer",
    "ne peut pas naviguer",
    "pas naviguer sur internet",
    "pas d'accès à internet",
    "pas d'acces a internet",
    "pas accès à internet",
    "sans accès à internet",
    "sans acces a internet",
    "connaissances jusqu'en",
    "données d'entraînement",
    "donnees d'entrainement",
    "ma formation s'arrête",
    "cutoff",
    "hors ligne",
)

_RE_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")

#: Plafond de liens vérifiés côté serveur (coût + durée bornés).
MAX_VERIFY_LINKS = 5
#: Timeout HTTP de vérification des liens cités.
VERIFY_TIMEOUT = 10.0


def build_probe(today: str | None = None) -> str:
    """Sonde datée : impossible à satisfaire sans navigation réelle."""
    if today is None:
        today = timezone.localdate().isoformat()
    return (
        f"Nous sommes le {today}. Liste les actualités IA marquantes des 2 derniers "
        f"jours maximum (strictement après le {today} moins 2 jours, jamais avant). "
        "Réponds en Markdown : une puce par actu avec sa date, chaque entité en lien "
        "[Nom exact](https://URL vue pendant ta recherche). Termine par une ligne "
        "« Sources : » avec un lien par source réellement utilisée. N'invente aucune URL."
    )


def extract_links(text: str) -> list[str]:
    """URL Markdown dédupliquées, ordre conservé."""
    seen: list[str] = []
    for _name, url in _RE_MD_LINK.findall(text or ""):
        url = url.rstrip(".,;:")
        if url not in seen:
            seen.append(url)
    return seen


def _refuses(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _REFUSALS)


def judge(reply: str, verified: int, checked: int) -> tuple[str, str]:
    """Verdict (statut, détail) depuis la réponse et la vérif liens.

    - ko : refus explicite ou erreur provider (gérée par l'appelant).
    - ok : liens cités ET au moins un vérifié existant, sans refus.
    - doute : le reste (liens invérifiables, réponse vague, anti-bot...).
    """
    links = extract_links(reply)
    if _refuses(reply):
        return "ko", "Le modèle déclare ne pas naviguer."
    if not links:
        return "doute", "Aucun lien cité : navigation indémontrable."
    if checked and not verified:
        return "doute", f"{len(links)} lien(s) cité(s) mais aucun vérifié (sites lents ou anti-bot ?)."
    if verified:
        return "ok", f"{verified}/{checked} lien(s) cité(s) vérifié(s) en ligne."
    return "doute", f"{len(links)} lien(s) cité(s), vérification impossible."


def verify_links(urls: list[str]) -> tuple[int, int]:
    """Vérifie l'existence des URL (HEAD puis GET). Retourne (vérifiés, testés).

    Borné à MAX_VERIFY_LINKS, garde SSRF réutilisée, échec silencieux par lien :
    un site lent n'invalide jamais les autres.
    """
    import httpx

    from .scheduler.engine import trigger_url_allowed

    checked = 0
    verified = 0
    for url in (urls or [])[:MAX_VERIFY_LINKS]:
        if not trigger_url_allowed(url):
            continue
        checked += 1
        try:
            with httpx.Client(timeout=VERIFY_TIMEOUT, follow_redirects=True) as client:
                resp = client.head(url, headers={"User-Agent": "JejeGPT/1.0 (+webtest)"})
                if resp.status_code in (405, 501):
                    resp = client.get(url, headers={"User-Agent": "JejeGPT/1.0 (+webtest)"})
                if 200 <= resp.status_code < 400:
                    verified += 1
        except Exception:
            logger.info("webtest lien invérifié : %s", url[:80])
    return verified, checked


def run_web_test(provider, model_id: str) -> tuple[str, str]:
    """Exécute le test complet. Retourne (statut, détail). Ne lève jamais."""
    from . import providers

    try:
        reply = providers.chat_complete(
            provider, model_id,
            [
                {"role": "system", "content": "Tu es un assistant avec accès web temps réel."},
                {"role": "user", "content": build_probe()},
            ],
            temperature=0.0,
            timeout=settings.JEJEGPT_HTTP_TIMEOUT,
            # Header Go obligatoire (400 sans x-opencode-session).
            session_id=f"webtest-{provider.pk}-{model_id}",
            route=providers.resolve_route(provider, model_id),
            # Web forcé : le test n'a de sens que si l'outil est demandé.
            options={"effort": "", "web": True, "thinking": False},
        )
    except providers.ProviderError as exc:
        return "ko", f"Appel impossible : {exc.message}"[:300]
    except Exception as exc:
        logger.exception("webtest crashed")
        return "doute", f"Test interrompu : {exc}"[:300]
    links = extract_links(reply)
    verified, checked = verify_links(links) if links else (0, 0)
    return judge(reply, verified, checked)
