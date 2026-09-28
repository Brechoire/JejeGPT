"""Client multi-routes : chat/completions, responses, messages (OpenAI-compatibles)."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import httpx
from django.utils import timezone

from .models import LLMModel, Provider

logger = logging.getLogger(__name__)

CHAT_PATH = "/chat/completions"
RESPONSES_PATH = "/responses"
MESSAGES_PATH = "/messages"
MODELS_PATH = "/models"
USER_AGENT = "JejeGPT/1.0"
MESSAGES_VERSION = "2023-06-01"
MESSAGES_MAX_TOKENS = 4096

# Table statique des routes non-chat relevée dans la doc OpenCode le 17/09/2026
# (https://opencode.ai/docs/go/ et https://opencode.ai/docs/zen/).
# PEUT SE PÉRIMER (la doc prévient que la liste change) : l'erreur guidée reste
# le filet, et `sync_models` ne touche jamais la route des modèles ajoutés à la main.
# model_id exact -> route ("responses" ou "messages"). Absent = compatible chat / inconnu.
GO_NON_CHAT_ROUTES = {
    "grok-4.6": "responses",
    "gpt-5.6-luna": "responses",
    "muse-spark-1.3-contributor": "responses",
    "muse-spark-1.2-contributor": "responses",
    "minimax-m3": "messages",
    "minimax-m2.7": "messages",
    "minimax-m2.5": "messages",
    "qwen3.8-max": "messages",
    "qwen3.8-flash": "messages",
    "qwen3.7-max": "messages",
    "qwen3.7-plus": "messages",
    "qwen3.6-plus": "messages",
    "union-alpha": "messages",
}
ZEN_NON_CHAT_ROUTES = {
    "gpt-5.6-sol": "responses",
    "gpt-5.6-terra": "responses",
    "gpt-5.6-luna": "responses",
    "gpt-5.5": "responses",
    "gpt-5.5-pro": "responses",
    "gpt-5.4": "responses",
    "gpt-5.4-pro": "responses",
    "gpt-5.4-mini": "responses",
    "gpt-5.4-nano": "responses",
    "gpt-5.3-codex": "responses",
    "gpt-5.3-codex-spark": "responses",
    "gpt-5.2": "responses",
    "gpt-5.2-codex": "responses",
    "gpt-5.1": "responses",
    "grok-4.6": "responses",
    "claude-opus-4-8": "messages",
    "claude-opus-4-7": "messages",
    "claude-opus-4-6": "messages",
    "claude-opus-4-5": "messages",
    "claude-opus-4-1": "messages",
    "claude-sonnet-4-6": "messages",
    "claude-sonnet-4-5": "messages",
    "claude-sonnet-4": "messages",
    "claude-haiku-4-5": "messages",
    "claude-3-5-haiku": "messages",
    "claude-fable-5": "messages",
    "gemini-3-flash": "messages",
    "gemini-3.1-pro": "messages",
    "gemini-3.5-flash": "messages",
    "gemini-3.5-flash-lite": "messages",
    "gemini-3.6-flash": "messages",
    "gemini-3.7-flash": "messages",
    "qwen3.6-plus": "messages",
    "qwen3.5-plus": "messages",
}


def lookup_route(base_url: str, model_id: str) -> str:
    """Route connue ('responses'/'messages') ou '' (chat ou inconnu, pas de badge)."""
    host = base_url.rstrip("/")
    if "/zen/go" in host:
        return GO_NON_CHAT_ROUTES.get(model_id, "")
    if "zen" in host:
        return ZEN_NON_CHAT_ROUTES.get(model_id, "")
    return ""


# Capacités par modèle relevées le 17/09/2026 (doc Go/Zen + catalogue tiers).
# PEUT SE PÉRIMER : `sync_models` ne l'applique qu'à la création, les cases
# manuelles (onglet Providers) priment ensuite. Clés : effort, web, thinking.
MODEL_CAPABILITIES: dict[str, dict[str, bool]] = {
    "muse-spark-1.3-contributor": {"effort": True, "web": True, "thinking": True},
    "muse-spark-1.2-contributor": {"effort": True, "web": True, "thinking": True},
    "gpt-5.6-luna": {"effort": True, "web": True, "thinking": True},
    "gpt-5.6-sol": {"effort": True, "web": True, "thinking": True},
    "gpt-5.6-terra": {"effort": True, "web": True, "thinking": True},
    "gpt-5.5": {"effort": True, "web": True, "thinking": True},
    "gpt-5.5-pro": {"effort": True, "web": True, "thinking": True},
    "gpt-5.4": {"effort": True, "web": True, "thinking": True},
    "gpt-5.4-pro": {"effort": True, "web": True, "thinking": True},
    "gpt-5.4-mini": {"effort": True, "web": True, "thinking": True},
    "gpt-5.4-nano": {"effort": True, "web": True, "thinking": True},
    "gpt-5.3-codex": {"effort": True, "web": True, "thinking": True},
    "gpt-5.3-codex-spark": {"effort": True, "web": True, "thinking": True},
    "gpt-5.2": {"effort": True, "web": True, "thinking": True},
    "gpt-5.2-codex": {"effort": True, "web": True, "thinking": True},
    "gpt-5.1": {"effort": True, "web": True, "thinking": True},
    "grok-4.6": {"effort": True, "web": True, "thinking": True},
    "qwen3.7-plus": {"effort": False, "web": False, "thinking": False},
    "qwen3.7-max": {"effort": False, "web": False, "thinking": False},
    "qwen3.6-plus": {"effort": False, "web": False, "thinking": False},
    "qwen3.8-max": {"effort": False, "web": False, "thinking": False},
    "qwen3.8-flash": {"effort": False, "web": False, "thinking": False},
    "minimax-m3": {"effort": False, "web": False, "thinking": False},
    "minimax-m2.7": {"effort": False, "web": False, "thinking": False},
    "minimax-m2.5": {"effort": False, "web": False, "thinking": False},
    "glm-5.3-flash": {"effort": False, "web": False, "thinking": False},
    "glm-5.3": {"effort": False, "web": False, "thinking": False},
    "glm-5.2": {"effort": False, "web": False, "thinking": False},
    "glm-5.1": {"effort": False, "web": False, "thinking": False},
    "kimi-k3": {"effort": False, "web": False, "thinking": True},
    "kimi-k2.7-code": {"effort": False, "web": False, "thinking": True},
    "kimi-k2.6": {"effort": False, "web": False, "thinking": True},
    "deepseek-v4-pro": {"effort": False, "web": False, "thinking": True},
    "deepseek-v4-flash": {"effort": False, "web": False, "thinking": True},
    "deepseek-v4.1-flash": {"effort": False, "web": False, "thinking": True},
    "hy4-preview": {"effort": False, "web": False, "thinking": True},
    "hy3": {"effort": False, "web": False, "thinking": True},
    "longcat-2.0": {"effort": False, "web": False, "thinking": False},
    "mimo-v2.5": {"effort": False, "web": False, "thinking": False},
    "mimo-v2.5-pro": {"effort": False, "web": False, "thinking": False},
    "union-alpha": {"effort": False, "web": False, "thinking": False},
}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
THINKING_BUDGETS = {"low": 1024, "medium": 4096, "high": 16384, "xhigh": 32768}


def _controls_for(route: str, caps: dict) -> dict[str, bool]:
    """Règle d'affichage pure (partagée par ui_controls et les badges du picker).

    Mêmes contrôles que le mapping d'envoi applique :
    - responses : effort si caps.effort, web si caps.web (pas de toggle thinking fiable).
    - messages : effort si caps.effort ou caps.thinking (budget), thinking si caps.thinking.
    - chat : rien (aucun paramètre standard).
    """
    if route == "responses":
        return {"effort": bool(caps.get("effort")), "web": bool(caps.get("web")), "thinking": False}
    if route == "messages":
        return {
            "effort": bool(caps.get("effort") or caps.get("thinking")),
            "web": bool(caps.get("web")),
            "thinking": bool(caps.get("thinking")),
        }
    return {"effort": False, "web": False, "thinking": False}


def ui_controls(provider: Provider | None, model_id: str) -> dict:
    """Contrôles à afficher : effort / web / thinking selon route + capacités.

    `known` = ligne cache existante OU entrée table statique (sinon « non vérifié »).
    """
    route = resolve_route(provider, model_id)
    caps = effective_caps(provider, model_id)
    cached = None
    if provider is not None and provider.pk:
        cached = provider.cached_models.filter(model_id=model_id).first()
    # Connu = info réelle (table statique ou ligne cache renseignée : route ou capacité).
    # Une ligne vide (sync sans métadonnées) vaut « non vérifié », pas « non supporté ».
    known = model_id in MODEL_CAPABILITIES or (
        cached is not None
        and bool(cached.route or cached.supports_effort or cached.supports_web or cached.supports_thinking)
    )
    if route == "responses":
        return {"route": route, **_controls_for(route, caps), "known": known}
    if route == "messages":
        return {"route": route, **_controls_for(route, caps), "known": known}
    return {"route": route, **_controls_for(route, caps), "known": known}


def model_controls(model: LLMModel, base_url: str = "") -> dict:
    """Badges picker depuis la ligne cache déjà chargée (zéro requête).

    Cohérent avec ui_controls : mêmes contrôles affichés que ceux proposés
    dans le composer. `known` = table statique OU ligne renseignée.
    """
    caps = {
        "effort": bool(model.supports_effort),
        "web": bool(model.supports_web),
        "thinking": bool(model.supports_thinking),
    }
    route = model.route or lookup_route(base_url, model.model_id)
    known = model.model_id in MODEL_CAPABILITIES or bool(
        route or caps["effort"] or caps["web"] or caps["thinking"]
    )
    return {"route": route, **_controls_for(route, caps), "known": known}


def _clean_options(options: dict | None) -> dict:
    options = options or {}
    effort = options.get("effort", "")
    return {
        "effort": effort if effort in EFFORT_LEVELS else "",
        "web": bool(options.get("web")),
        "thinking": bool(options.get("thinking", True)),
    }


def _responses_extra(caps: dict, options: dict) -> dict:
    body: dict = {}
    if caps.get("effort") and options["effort"]:
        body["reasoning"] = {"effort": options["effort"]}
    if caps.get("web") and options["web"]:
        body["tools"] = [{"type": "web_search"}]
    return body


def _messages_extra(caps: dict, options: dict) -> dict:
    body: dict = {}
    show_effort = caps.get("effort") or caps.get("thinking")
    if caps.get("thinking"):
        if options["thinking"]:
            level = options["effort"] if options["effort"] else "medium"
            body["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGETS[level]}
        else:
            body["thinking"] = {"type": "disabled"}
    elif show_effort and options["effort"]:
        body["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGETS[options["effort"]]}
    if caps.get("web") and options["web"]:
        # Nom d'outil à valider contre Zen réel ; l'erreur guidée signalera un rejet.
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    return body


def model_capabilities(model_id: str) -> dict[str, bool]:
    """Capacités statiques d'un modèle (défaut tout-False si inconnu)."""
    caps = MODEL_CAPABILITIES.get(model_id)
    if caps is None:
        return {"effort": False, "web": False, "thinking": False}
    return dict(caps)


# Paramètres OpenRouter signalant un mode raisonnement interne
# (doc : supported_parameters = reasoning, include_reasoning).
# Pas de signal fiable pour les paliers d'effort (reasoning unifié effort/max_tokens)
# ni pour le web search (plugin par requête, pas par modèle) → effort/web restent
# statiques + manuels.
_OPENROUTER_REASONING_PARAMS = frozenset({"reasoning", "include_reasoning"})


def _is_openrouter(provider: Provider | None) -> bool:
    return provider is not None and "openrouter" in _base(str(provider.base_url))


def caps_from_openrouter(entry: dict | None) -> dict[str, bool]:
    """Capacités déduites d'une entrée OpenRouter GET /models (conservateur).

    reasoning* → thinking=True. effort/web toujours False ici : la table statique
    (prioritaire) couvre les cas effort=True, et le web reste statique + manuel.
    Entrée absente ou minimale → tout-False (repli existant).
    """
    params: set[str] = set()
    if isinstance(entry, dict):
        raw = entry.get("supported_parameters") or []
        params = {str(p).lower() for p in raw if isinstance(p, str)}
    thinking = bool(params & _OPENROUTER_REASONING_PARAMS)
    return {"effort": False, "web": False, "thinking": thinking}


def effective_caps(provider: Provider | None, model_id: str) -> dict[str, bool]:
    """Capacités effectives : ligne cache (incl. overrides manuels) > table > tout-False."""
    if provider is not None and provider.pk:
        cached = provider.cached_models.filter(model_id=model_id).first()
        if cached is not None:
            return {
                "effort": bool(cached.supports_effort),
                "web": bool(cached.supports_web),
                "thinking": bool(cached.supports_thinking),
            }
    return model_capabilities(model_id)


def resolve_route(provider: Provider | None, model_id: str) -> str:
    """Route effective : cache LLMModel > table statique > '' (chat)."""
    if provider is not None and provider.pk:
        cached = provider.cached_models.filter(model_id=model_id).first()
        if cached and cached.route:
            return cached.route
        return lookup_route(str(provider.base_url), model_id)
    if provider is not None:
        return lookup_route(str(provider.base_url), model_id)
    return ""


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Sépare le system prompt (champ `system` de l'API Messages) du reste."""
    system = "\n\n".join(
        str(m.get("content", "")) for m in messages
        if m.get("role") == "system" and m.get("content")
    )
    rest = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return system, rest


@dataclass
class ProviderError(Exception):
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def _headers(provider: Provider, session_id: int | str | None = None) -> dict:
    """Headers sortants. La doc Go/Zen exige un User-Agent propre (pas celui du SDK)
    et un x-opencode-session stable par conversation (routage + cache, anti-abus)."""
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    if session_id is not None:
        headers["x-opencode-session"] = str(session_id)
    return headers


def _safe_body(response: httpx.Response) -> str:
    """Corps de réponse sans jamais lever (réponse streaming non lue, etc.)."""
    try:
        return response.text
    except Exception:
        return ""


def _extract_message(raw: str) -> str | None:
    """Extrait le message des enveloppes d'erreur croisées.

    Formes vues : {"error": {"message": ...}}, {"error": "..."},
    {"type": "error", "message": "..."} (Zen 200+erreur),
    {"detail": ...} (FastAPI, str ou liste), {"response": {"error": {"message": ...}}}.
    Corps bruts uniquement (jamais de headers/clés) — voir _log_error_body.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if msg:
            return str(msg)
    elif isinstance(err, str) and err:
        return err
    if data.get("type") == "error" and isinstance(data.get("message"), str) and data["message"]:
        return data["message"]
    detail = data.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list):
        text = "; ".join(str(d) for d in detail if d)[:300]
        if text:
            return text
    nested = data.get("response")
    if isinstance(nested, dict):
        nested_err = nested.get("error")
        if isinstance(nested_err, dict) and nested_err.get("message"):
            return str(nested_err["message"])
    return None


def _log_error_body(raw: str) -> None:
    """Log console (tronqué) pour diagnostiquer les formes d'erreur inconnues."""
    if raw:
        logger.warning("provider error body: %s", raw[:500])


def _empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "duration_ms": 0}


def _fill_usage(
    collector: dict | None,
    prompt: object = 0,
    completion: object = 0,
    total: object = None,
) -> None:
    """Remplit le collecteur (tolérant : valeurs non numériques -> 0)."""
    if collector is None:
        return

    def _num(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    prompt_n, completion_n = _num(prompt), _num(completion)
    total_n = _num(total) if total is not None else prompt_n + completion_n
    collector["prompt_tokens"] = prompt_n
    collector["completion_tokens"] = completion_n
    collector["total_tokens"] = total_n


def _fill_usage_openai(collector: dict | None, usage: object) -> None:
    if isinstance(usage, dict):
        _fill_usage(
            collector, usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0), usage.get("total_tokens"),
        )


def _fill_usage_responses(collector: dict | None, usage: object) -> None:
    if isinstance(usage, dict):
        _fill_usage(
            collector, usage.get("input_tokens", 0),
            usage.get("output_tokens", 0), usage.get("total_tokens"),
        )


def _fill_usage_messages(collector: dict | None, usage: object, which: str) -> None:
    """Fusionne l'usage Messages (input au start, output cumulé au fil de l'eau)."""
    if collector is None or not isinstance(usage, dict):
        return
    if which == "start":
        _fill_usage(collector, usage.get("input_tokens", 0), collector.get("completion_tokens", 0))
    else:
        _fill_usage(collector, collector.get("prompt_tokens", 0), usage.get("output_tokens", 0))


ROUTE_HINT = (
    " — Si ce provider expose plusieurs formats (ex. Zen/Go : /responses, /messages),"
    " choisissez un modèle de la route /chat/completions."
)


def _is_zen_go(provider: Provider | None) -> bool:
    """Sniffing d'hôte cantonné aux messages UX (console, limites, Use balance)."""
    if provider is None:
        return False
    host = _base(provider.base_url)
    return "zen" in host or "/go/" in host or host.rstrip("/").endswith("/go")


def _friendly_error(exc: Exception, provider: Provider | None = None) -> ProviderError:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return ProviderError("Clé API invalide (401). Vérifiez la clé du provider.", 401)
        if code == 404:
            return ProviderError("Endpoint ou modèle introuvable (404). Vérifiez base_url et model_id.", 404)
        if code == 429:
            msg = "Quota dépassé / trop de requêtes (429). Réessayez plus tard."
            if _is_zen_go(provider):
                msg += (
                    " Sur Go/Zen : limite mensuelle atteinte ? Vérifiez votre utilisation"
                    " (console opencode.ai/auth), basculez sur un modèle gratuit (ex. union-alpha)"
                    " ou activez Use balance (crédits Zen) dans la console."
                )
            return ProviderError(msg, 429)
        raw = _safe_body(exc.response)
        detail = _extract_message(raw) or raw[:300] or f"HTTP {code}"
        return ProviderError(f"Erreur provider HTTP {code} : {detail}{ROUTE_HINT}", code)
    if isinstance(exc, httpx.ConnectError):
        return ProviderError("Connexion refusée : le provider est-il démarré ? (ex. LM Studio, Ollama).")
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("Timeout : le provider met trop de temps à répondre.")
    return ProviderError(f"Erreur provider : {exc}")


def fetch_model_entries(provider: Provider, timeout: float = 30.0) -> list[dict]:
    """Entrées brutes via GET {base_url}/models (id + métadonnées éventuelles). Lève ProviderError."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(_base(provider.base_url) + MODELS_PATH, headers=_headers(provider))
            resp.raise_for_status()
            data = resp.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _friendly_error(exc, provider) from exc
    items = data.get("data", []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict) and i.get("id")]


def fetch_models(provider: Provider, timeout: float = 30.0) -> list[str]:
    """Retourne les model_id via GET {base_url}/models. Lève ProviderError."""
    return [str(i.get("id", "")) for i in fetch_model_entries(provider, timeout=timeout)]


def sync_models(provider: Provider, timeout: float = 30.0) -> tuple[int, str]:
    """Upsert le cache LLMModel. Retourne (nb, erreur). Ne lève pas."""
    try:
        ids = fetch_models(provider, timeout=timeout)
    except ProviderError as exc:
        provider.last_error = exc.message
        provider.save(update_fields=["last_error", "updated_at"])
        return 0, exc.message
    # Métadonnées riches OpenRouter (2e GET, best-effort) : seul ce provider expose
    # supported_parameters. Échec silencieux → repli table statique + manuel.
    entries_by_id: dict[str, dict] = {}
    if _is_openrouter(provider):
        try:
            entries_by_id = {str(e.get("id")): e for e in fetch_model_entries(provider, timeout=timeout)}
        except ProviderError:
            entries_by_id = {}
    for mid in ids:
        obj, created = LLMModel.objects.update_or_create(
            provider=provider,
            model_id=mid,
            defaults={
                "display_name": mid,
                "route": lookup_route(str(provider.base_url), mid),
            },
        )
        if created:
            # Les capacités ne s'appliquent qu'à la création : les réglages manuels
            # postérieurs (cases onglet Providers) ne sont jamais écrasés.
            # Priorité : table statique (source de vérité) > métadonnées OpenRouter
            # (thinking auto-détecté) > tout-False.
            if mid in MODEL_CAPABILITIES:
                caps = model_capabilities(mid)
            else:
                entry = entries_by_id.get(mid)
                caps = caps_from_openrouter(entry) if entry is not None else model_capabilities(mid)
            obj.supports_effort = caps["effort"]
            obj.supports_web = caps["web"]
            obj.supports_thinking = caps["thinking"]
            obj.save(update_fields=["supports_effort", "supports_web", "supports_thinking"])
    provider.cached_models.exclude(model_id__in=ids).delete()
    provider.last_fetch_at = timezone.now()
    provider.last_error = ""
    provider.save()
    return len(ids), ""


def _responses_text(data: dict) -> str:
    """Texte d'une réponse Responses non-streamée (output_text ou items)."""
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    parts: list[str] = []
    refused: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and part.get("text"):
                parts.append(str(part["text"]))
            elif part.get("type") == "refusal" and part.get("refusal"):
                refused.append(str(part["refusal"]))
    if parts:
        return "".join(parts)
    if refused:
        return "Refus : " + "".join(refused)
    return ""


def _messages_text(data: dict) -> str:
    """Texte d'une réponse Messages non-streamée (blocs content)."""
    parts = [
        str(b.get("text", ""))
        for b in data.get("content", []) or []
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return "".join(parts)


def _check_envelope(data: object, raw: str) -> None:
    """Lève ProviderError si le payload porte un vrai message d'erreur.

    Note : "error": null (réponse completed standard) n'est PAS une erreur —
    on teste le message extrait, pas la présence de la clé.
    """
    if isinstance(data, dict) and _extract_message(raw) is not None:
        _log_error_body(raw)
        raise ProviderError(f"{_extract_message(raw)}{ROUTE_HINT}")


def _iter_sse(resp: httpx.Response) -> Iterator[tuple[str, str]]:
    """Itère les trames SSE en couples (event, data)."""
    event = "message"
    for line in resp.iter_lines():
        if not line:
            continue
        if line.startswith("event:"):
            event = line[6:].strip() or "message"
            continue
        if line.startswith("data:"):
            yield event, line[5:].strip()
            event = "message"


def _post_json(
    provider: Provider,
    path: str,
    payload: dict,
    timeout: float,
    session_id: int | str | None,
    extra_headers: dict | None = None,
) -> dict:
    """POST JSON non-streamé avec erreurs normalisées."""
    headers = _headers(provider, session_id)
    if extra_headers:
        headers.update(extra_headers)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(_base(provider.base_url) + path, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _friendly_error(exc, provider) from exc
    if not isinstance(data, dict):
        raise ProviderError(f"Réponse inattendue du provider.{ROUTE_HINT}")
    _check_envelope(data, json.dumps(data))
    return data


def _complete_responses(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float,
    timeout: float,
    session_id: int | str | None,
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> str:
    opts = _clean_options(options)
    caps = effective_caps(provider, model_id)
    payload: dict = {
        "model": model_id, "input": messages,
        "temperature": temperature, "stream": False,
    }
    payload.update(_responses_extra(caps, opts))
    data = _post_json(provider, RESPONSES_PATH, payload, timeout, session_id)
    _fill_usage_responses(usage_collector, data.get("usage"))
    text = _responses_text(data)
    if not text:
        raise ProviderError(f"Réponse vide du provider.{ROUTE_HINT}")
    return text


def _complete_messages(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float,
    timeout: float,
    session_id: int | str | None,
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> str:
    opts = _clean_options(options)
    caps = effective_caps(provider, model_id)
    extra = _messages_extra(caps, opts)
    system, rest = _split_system(messages)
    thinking_on = extra.get("thinking", {}).get("type") == "enabled"
    payload: dict = {
        "model": model_id,
        "messages": rest,
        "max_tokens": MESSAGES_MAX_TOKENS,
        # Thinking activé exige temperature=1 côté Anthropic.
        "temperature": 1.0 if thinking_on else temperature,
        "stream": False,
    }
    if system:
        payload["system"] = system
    payload.update(extra)
    data = _post_json(
        provider, MESSAGES_PATH, payload, timeout, session_id,
        extra_headers={"anthropic-version": MESSAGES_VERSION},
    )
    _fill_usage(usage_collector, (data.get("usage") or {}).get("input_tokens", 0),
                (data.get("usage") or {}).get("output_tokens", 0))
    text = _messages_text(data)
    if not text:
        raise ProviderError(f"Réponse vide du provider.{ROUTE_HINT}")
    return text


def _open_stream(
    provider: Provider,
    path: str,
    payload: dict,
    timeout: float,
    session_id: int | str | None,
    extra_headers: dict | None = None,
):
    """Contexte de stream avec corps d'erreur bufferisé (cf. bug ResponseNotRead)."""
    from contextlib import contextmanager

    headers = dict(_headers(provider, session_id), Accept="text/event-stream")
    if extra_headers:
        headers.update(extra_headers)

    @contextmanager
    def _ctx():
        with httpx.stream(
            "POST", _base(provider.base_url) + path,
            headers=headers, json=payload, timeout=timeout,
        ) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    exc.response.read()
                except Exception:
                    pass
                raise _friendly_error(exc, provider) from exc
            yield resp

    return _ctx()


def _stream_responses(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float,
    timeout: float,
    session_id: int | str | None,
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> Iterator[str]:
    opts = _clean_options(options)
    caps = effective_caps(provider, model_id)
    payload: dict = {"model": model_id, "input": messages, "temperature": temperature, "stream": True}
    payload.update(_responses_extra(caps, opts))
    try:
        with _open_stream(provider, RESPONSES_PATH, payload, timeout, session_id) as resp:
            for _event, data in _iter_sse(resp):
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                etype = obj.get("type", "")
                if etype == "response.output_text.delta" and obj.get("delta"):
                    yield str(obj["delta"])
                elif etype == "response.refusal.delta" and obj.get("delta"):
                    yield "Refus : " + str(obj["delta"])
                elif etype in ("error", "response.failed"):
                    # Vrai event d'erreur (un completed avec "error": null a un autre type).
                    raw = json.dumps(obj)
                    flat = obj.get("message") if isinstance(obj.get("message"), str) else None
                    _log_error_body(raw)
                    raise ProviderError(f"{_extract_message(raw) or flat or 'Erreur provider.'}{ROUTE_HINT}")
                elif etype == "response.completed":
                    _fill_usage_responses(usage_collector, obj.get("usage"))
                    break
    except ProviderError:
        raise
    except Exception as exc:
        raise _friendly_error(exc, provider) from exc


def _stream_messages(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float,
    timeout: float,
    session_id: int | str | None,
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> Iterator[str]:
    opts = _clean_options(options)
    caps = effective_caps(provider, model_id)
    extra = _messages_extra(caps, opts)
    system, rest = _split_system(messages)
    thinking_on = extra.get("thinking", {}).get("type") == "enabled"
    payload: dict = {
        "model": model_id,
        "messages": rest,
        "max_tokens": MESSAGES_MAX_TOKENS,
        # Thinking activé exige temperature=1 côté Anthropic.
        "temperature": 1.0 if thinking_on else temperature,
        "stream": True,
    }
    if system:
        payload["system"] = system
    payload.update(extra)
    try:
        with _open_stream(
            provider, MESSAGES_PATH, payload, timeout, session_id,
            extra_headers={"anthropic-version": MESSAGES_VERSION},
        ) as resp:
            for _event, data in _iter_sse(resp):
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                etype = obj.get("type", "")
                if etype == "message_start":
                    _fill_usage_messages(
                        usage_collector,
                        (obj.get("message") or {}).get("usage") if isinstance(obj.get("message"), dict) else None,
                        "start",
                    )
                elif etype == "message_delta":
                    _fill_usage_messages(usage_collector, obj.get("usage"), "delta")
                elif etype == "content_block_delta":
                    delta = obj.get("delta", {})
                    if isinstance(delta, dict) and delta.get("type") == "text_delta" and delta.get("text"):
                        yield str(delta["text"])
                elif etype == "error":
                    raw = json.dumps(obj)
                    flat = obj.get("message") if isinstance(obj.get("message"), str) else None
                    nested = obj.get("error", {}) if isinstance(obj.get("error"), dict) else {}
                    nested_msg = nested.get("message") if isinstance(nested.get("message"), str) else None
                    _log_error_body(raw)
                    raise ProviderError(
                        f"{_extract_message(raw) or flat or nested_msg or 'Erreur provider.'}{ROUTE_HINT}"
                    )
                elif etype == "message_stop":
                    break
    except ProviderError:
        raise
    except Exception as exc:
        raise _friendly_error(exc, provider) from exc


def chat_complete(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float = 0.7,
    timeout: float = 120.0,
    session_id: int | str | None = None,
    route: str = "",
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> str:
    """Appel non-streamé, dispatché selon la route (chat / responses / messages)."""
    started = time.perf_counter()
    try:
        if route == "responses":
            return _complete_responses(
                provider, model_id, messages, temperature, timeout, session_id, options, usage_collector)
        if route == "messages":
            return _complete_messages(
                provider, model_id, messages, temperature, timeout, session_id, options, usage_collector)
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    _base(provider.base_url) + CHAT_PATH,
                    headers=_headers(provider, session_id),
                    json={"model": model_id, "messages": messages, "temperature": temperature, "stream": False},
                )
                resp.raise_for_status()
                data = resp.json()
            _fill_usage_openai(usage_collector, data.get("usage"))
            return data["choices"][0]["message"]["content"] or ""
        except ProviderError:
            raise
        except Exception as exc:
            raise _friendly_error(exc, provider) from exc
    finally:
        if usage_collector is not None:
            usage_collector["duration_ms"] = int((time.perf_counter() - started) * 1000)


def _chat_stream_cm(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float,
    timeout: float,
    session_id: int | str | None,
):
    """Construit le contexte de stream chat (helper pour garder le `with` lisible)."""
    return httpx.stream(
        "POST",
        _base(provider.base_url) + CHAT_PATH,
        headers=dict(_headers(provider, session_id), Accept="text/event-stream"),
        json={
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        timeout=timeout,
    )


def chat_stream(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    temperature: float = 0.7,
    timeout: float = 120.0,
    session_id: int | str | None = None,
    route: str = "",
    options: dict | None = None,
    usage_collector: dict | None = None,
) -> Iterator[str]:
    """Générateur de tokens via SSE (data: {...} + data: [DONE])."""
    started = time.perf_counter()
    try:
        if route == "responses":
            yield from _stream_responses(
                provider, model_id, messages, temperature, timeout, session_id, options, usage_collector)
            return
        if route == "messages":
            yield from _stream_messages(
                provider, model_id, messages, temperature, timeout, session_id, options, usage_collector)
            return
        try:
            with _chat_stream_cm(
                provider, model_id, messages, temperature, timeout, session_id
            ) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # Bufferise le corps d'erreur : sur une réponse streaming réelle,
                    # .text lèverait ResponseNotRead (« without having called read() »).
                    try:
                        exc.response.read()
                    except Exception:
                        pass
                    raise _friendly_error(exc, provider) from exc
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    msg = _extract_message(payload) if isinstance(chunk, dict) else None
                    if msg is None and isinstance(chunk, dict) and chunk.get("error"):
                        # Enveloppe sans message extractible : erreur générique plutôt que silence.
                        msg = "Erreur provider."
                    if msg is not None:
                        # Passerelle qui répond 200 + JSON d'erreur (plus de message vide silencieux).
                        # Note : "error": null (réponse completed standard) n'est pas une erreur.
                        _log_error_body(payload)
                        raise ProviderError(f"{msg}{ROUTE_HINT}")
                    try:
                        delta = chunk["choices"][0]["delta"].get("content", "")
                    except (KeyError, IndexError, TypeError):
                        # Chunk final d'usage (stream_options.include_usage) : pas de choices.
                        if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                            _fill_usage_openai(usage_collector, chunk["usage"])
                        continue
                    if delta:
                        yield delta
        except ProviderError:
            raise
        except Exception as exc:
            raise _friendly_error(exc, provider) from exc
    except ProviderError:
        raise
    except Exception as exc:
        raise _friendly_error(exc, provider) from exc
    finally:
        if usage_collector is not None:
            usage_collector["duration_ms"] = int((time.perf_counter() - started) * 1000)


def iter_sse_lines(tokens: Iterable[str]) -> Iterator[str]:
    """Formate des tokens en trames SSE pour StreamingHttpResponse."""
    for tok in tokens:
        yield f"data: {json.dumps({'token': tok})}\n\n"
    yield "event: done\ndata: [DONE]\n\n"
