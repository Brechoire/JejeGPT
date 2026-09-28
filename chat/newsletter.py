"""Synthèse newsletter médiathèque à partir d'une réponse du chat (V1, sans serveur mail)."""
from __future__ import annotations

import re
from urllib.parse import quote

NEWSLETTER_MAX_SOURCE_CHARS = 12000
MAILTO_MAX_BODY_CHARS = 1500

NEWSLETTER_SYSTEM = (
    "Tu es un rédacteur newsletter pour médiathèque. Transforme le contenu fourni "
    "en newsletter en français, ton chaleureux et accessible, sans inventer ni faits "
    "ni URL. Structure exacte à respecter :\n"
    "1. Première ligne : « Objet : <objet court, 80 caractères max> ».\n"
    "2. « ## Bonjour » : intro de 2-3 phrases présentant la sélection.\n"
    "3. « ## À la une » : jusqu'à 5 actus, chacune avec « ### <titre> », la date si "
    "elle est connue, un résumé en 3 à 5 phrases, puis « Pourquoi c'est intéressant : … ». "
    "Reprends les sources telles quelles (liens Markdown d'origine, aucune URL inventée).\n"
    "4. « ## En résumé » : 3 à 5 puces des points essentiels.\n"
    "5. « ## Tendances à surveiller » : 2 à 3 puces courtes.\n"
    "6. Termine la partie Markdown par une ligne « Sources : » avec un lien Markdown "
    "[nom](https://…) par source réellement utilisée.\n"
    "7. Après une ligne exactement « ---TEXTE--- », reprends objet, intro, actus et "
    "résumé en texte brut sans Markdown (pour le corps du mail)."
)

NEWSLETTER_SYSTEM_SHORT = (
    "Tu es un rédacteur newsletter pour médiathèque. Transforme le contenu fourni "
    "en mail court en texte brut, sans aucun Markdown (pas de #, *, |, [liens]). "
    "Contraintes strictes :\n"
    "1. Première ligne : « Objet : <objet court, 80 caractères max> ».\n"
    "2. Puis une intro de 1-2 phrases.\n"
    "3. Puis 3 actus maximum, chacune : titre sur une ligne, 2-3 phrases de résumé, "
    "« Pourquoi c'est intéressant : … » sur une ligne.\n"
    "4. Sources : URLs brutes (https://…) une par ligne, réellement utilisées, aucune inventée.\n"
    "5. Corps total après la ligne Objet : 1200 caractères maximum."
)

NEWSLETTER_SYSTEM_RICH = (
    "Tu es un rédacteur newsletter pour médiathèque. Transforme le contenu fourni "
    "en newsletter en Markdown français optimisée pour un rendu e-mail, ton chaleureux "
    "et accessible, sans inventer ni faits ni URL. Contraintes :\n"
    "1. Titres ## et ### avec une intro de 2-3 phrases.\n"
    "2. Présente la sélection (jusqu'à 5 actus) en tableau Markdown "
    "(colonnes Titre, Résumé, Pourquoi c'est intéressant) quand c'est pertinent, "
    "sinon en sections « ### <titre> ».\n"
    "3. Liens systématiquement absolus en https:// (liens Markdown [nom](https://…)), "
    "aucune URL inventée.\n"
    "4. Pas de HTML brut, pas de blocs repliables, pas de notes de bas de page.\n"
    "5. Termine par une ligne « Sources : » avec un lien par source utilisée."
)

CHANNEL_SYSTEMS = {
    "short": NEWSLETTER_SYSTEM_SHORT,
    "rich": NEWSLETTER_SYSTEM_RICH,
    "full": NEWSLETTER_SYSTEM,
}
CHANNELS = tuple(CHANNEL_SYSTEMS)

# Vocabulaire des boutons/URLs (mailto|html|eml) vers les prompts internes.
URL_CHANNELS = ("mailto", "html", "eml")
_URL_ALIASES = {"mailto": "short", "html": "rich", "eml": "full"}

_RE_SUBJECT = re.compile(r"^Objet\s*:\s*(.+?)\s*$", re.MULTILINE)
TEXT_SEPARATOR = "---TEXTE---"


def split_newsletter(full_text: str) -> tuple[str, str, str]:
    """Découpe (objet, partie Markdown, partie texte brut) d'une synthèse.

    Replis si le modèle ne respecte pas la structure : objet = première ligne
    non vide (tronquée à 80 caractères), texte brut = partie Markdown.
    """
    text = (full_text or "").strip()
    subject = ""
    match = _RE_SUBJECT.search(text)
    if match:
        subject = match.group(1).strip()[:80]
    if TEXT_SEPARATOR in text:
        md_part, text_part = text.split(TEXT_SEPARATOR, 1)
        md_part, text_part = md_part.strip(), text_part.strip()
    else:
        md_part, text_part = text, text
    if not subject:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        subject = re.sub(r"^#+\s*", "", first_line)[:80] or "Newsletter médiathèque"
    if not text_part.strip():
        text_part = md_part
    return subject, md_part, text_part


def _call_provider(conv, system: str, source_content: str) -> str:
    """Un appel synthèse au provider de la conversation (usage enregistré)."""
    from . import providers
    from django.conf import settings

    options = {"effort": "", "web": False, "thinking": bool(conv.thinking_enabled)}
    effort = (conv.reasoning_effort or "")
    if effort in providers.EFFORT_LEVELS:
        options["effort"] = effort
    usage = providers._empty_usage()
    try:
        reply = providers.chat_complete(
            conv.provider, conv.model_id,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "Contenu à transformer en newsletter :\n\n" + (source_content or "")[:NEWSLETTER_MAX_SOURCE_CHARS]},
            ],
            temperature=conv.temperature, timeout=settings.JEJEGPT_HTTP_TIMEOUT * 4,
            session_id=conv.pk,
            route=providers.resolve_route(conv.provider, conv.model_id),
            options=options,
            usage_collector=usage,
        )
    except providers.ProviderError:
        raise
    from .views import _record_usage

    _record_usage(conv, usage, success=True)
    conv.save()
    return reply


def build_newsletter_for_channel(conv, source_content: str, channel: str) -> str:
    """Régénère une synthèse adaptée au canal, à chaque appel.

    Accepte le vocabulaire interne (short|rich|full) et celui des boutons
    (mailto|html|eml).
    """
    key = _URL_ALIASES.get(channel, channel)
    if key not in CHANNEL_SYSTEMS:
        raise ValueError(f"Canal newsletter inconnu : {channel}.")
    return _call_provider(conv, CHANNEL_SYSTEMS[key], source_content)


def build_newsletter_text(conv, source_content: str) -> str:
    """Appelle le provider de la conversation pour transformer une réponse en newsletter."""
    return build_newsletter_for_channel(conv, source_content, "full")


def mailto_href(subject: str, body_text: str) -> str:
    """Lien mailto: (objet + corps tronqué, ouvre Outlook par défaut)."""
    body = (body_text or "").strip()
    if len(body) > MAILTO_MAX_BODY_CHARS:
        body = body[:MAILTO_MAX_BODY_CHARS].rstrip() + "… (texte complet dans le .eml)"
    return f"mailto:?subject={quote(subject)}&body={quote(body)}"
