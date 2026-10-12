"""Extraction 100 % LLM d'une planification depuis une demande en langage naturel."""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

PARIS = ZoneInfo("Europe/Paris")

FREQUENCIES = ("daily", "weekly", "monthly", "once")

PARSE_SYSTEM = (
    "Tu es un extracteur de planification. Réponds UNIQUEMENT avec un objet JSON valide, "
    "sans markdown ni texte autour. Schéma : {\"name\": titre court, "
    "\"prompt\": consigne à exécuter reformulée, "
    "\"frequency\": \"daily\" | \"weekly\" | \"monthly\" | \"once\", "
    "\"days\": [0-6] (lundi=0, seulement si weekly), "
    "\"month_day\": 1-28 (seulement si monthly), "
    "\"time\": \"HH:MM\" 24h (requis sauf si once), "
    "\"once_at\": \"AAAA-MM-JJ HH:MM\" (requis si once)}. "
    "Fuseau Europe/Paris, aujourd'hui le {today}. "
    "Si la demande ne contient aucune planification temporelle, réponds {\"error\": \"...\"}."
)
_TODAY_MARKER = "{today}"

WEB_KEYWORDS = ("actus", "actualités", "actualites", "news", "nouvelles")

SCHEDULE_HINTS = (
    "tous les jours", "chaque jour", "toutes les semaines", "chaque semaine",
    "tous les matins", "tous les soirs", "quotidien", "hebdomadaire",
    "tous les mois", "chaque mois", "mensuel", "mensuelle",
    "rappelle-moi", "rappelle moi", "planifie",
    "chaque lundi", "chaque mardi", "chaque mercredi", "chaque jeudi",
    "chaque vendredi", "chaque samedi", "chaque dimanche",
)


def detect_schedule_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in SCHEDULE_HINTS)

_RE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def needs_web_search(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in WEB_KEYWORDS)


def _strip_fences(raw: str) -> str:
    return _RE_FENCE.sub("", raw or "").strip()


def _valid_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value.strip())
    return match.group(0) if match else None


def _valid_days(value: object) -> list[int] | None:
    if not isinstance(value, list) or not value:
        return None
    days = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 6:
            return None
        days.append(item)
    return sorted(set(days))


def parse_schedule_request(provider, model_id: str, user_text: str, today=None) -> dict | None:
    """Demande au LLM un JSON de planification, validé strictement. None si incompréhensible."""
    from .. import providers

    if today is None:
        today = timezone.now().astimezone(PARIS).date().isoformat()
    if provider is None or not model_id:
        return None
    try:
        # session_id requis : le proxy Go refuse sans x-opencode-session (400).
        raw = providers.chat_complete(
            provider, model_id,
            [
                {"role": "system", "content": PARSE_SYSTEM.replace(_TODAY_MARKER, today)},
                {"role": "user", "content": user_text},
            ],
            temperature=0.0,
            timeout=settings.JEJEGPT_HTTP_TIMEOUT,
            session_id=f"schedule-parse-{provider.pk}" if provider.pk else None,
            route=providers.resolve_route(provider, model_id),
        )
    except providers.ProviderError:
        return None
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or "error" in data:
        return None
    return validate_schedule_data(data)


def validate_schedule_data(data: dict) -> dict | None:
    frequency = data.get("frequency")
    if frequency not in FREQUENCIES:
        return None
    name = data.get("name")
    prompt = data.get("prompt")
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        return None
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    normalized = {
        "name": name.strip(),
        "prompt": prompt.strip(),
        "frequency": frequency,
        "days": [],
        "month_day": None,
        "time": None,
        "once_at": None,
    }
    if frequency == "once":
        raw_once = data.get("once_at")
        if not isinstance(raw_once, str):
            return None
        try:
            once_at = datetime.strptime(raw_once.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
        except ValueError:
            return None
        if once_at <= timezone.now():
            return None
        normalized["once_at"] = once_at
        return normalized
    time = _valid_time(data.get("time"))
    if time is None:
        return None
    normalized["time"] = time
    if frequency == "weekly":
        days = _valid_days(data.get("days"))
        if days is None:
            return None
        normalized["days"] = days
    if frequency == "monthly":
        month_day = data.get("month_day")
        if isinstance(month_day, bool) or not isinstance(month_day, int):
            try:
                month_day = int(str(month_day or "").strip())
            except (ValueError, TypeError):
                return None
        if not 1 <= month_day <= 28:
            return None
        normalized["month_day"] = month_day
    return normalized


DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def describe_schedule(frequency: str, days: list[int], time: str | None, once_at=None, month_day=None) -> str:
    if frequency == "daily":
        return f"Tous les jours à {time}"
    if frequency == "weekly":
        names = ", ".join(DAY_NAMES[d] for d in sorted(days))
        return f"Chaque {names} à {time}"
    if frequency == "monthly":
        return f"Chaque {month_day} du mois à {time}"
    if once_at is not None:
        return f"Le {once_at.strftime('%d/%m/%Y')} à {once_at.strftime('%H:%M')}"
    return "Une fois"
