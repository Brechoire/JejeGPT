"""Recherche d'images web illustratives (style ChatGPT).

V1 : hotlink direct, aucun binaire persisté, dégradation gracieuse
(échec/timeout → liste vide, la réponse texte est toujours livrée).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list[ImageResult]]] = {}
_CACHE_TTL = 3600

_EXPLICIT_RE = re.compile(
    r"montre|illustre|photo|image|visuel|screenshot|capture|"
    r"\xe0 quoi.*ressemble|dessin|carte|sch\xe9ma",
    re.IGNORECASE,
)
_VISUAL_RE = re.compile(
    r"ruine|sous-marin|nautilon|no man|jeu|plan\xe8te|animal|plante|fleur|"
    r"oiseau|champignon|insecte|recette|plat|ch\xe2teau|ville|paysage|"
    r"monument|mus\xe9e|voiture|avion|bateau|trouver|localiser|o\xf9 ",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"code|python|fonction|classe|erreur|traceback|sql|requ\xeate sql|"
    r"\xe9quation|math|d\xe9riv\xe9e|int\xe9grale|regex",
    re.IGNORECASE,
)


@dataclass
class ImageResult:
    title: str
    image_url: str
    source_url: str
    source_name: str = ""
    thumb_url: str = ""


def _valid_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def needs_images(prompt: str) -> bool:
    """Heuristique : True si une illustration aiderait probablement."""
    text = (prompt or "").strip()
    if len(text) < 8:
        return False
    if _EXPLICIT_RE.search(text):
        return True
    if _CODE_RE.search(text):
        return False
    return bool(_VISUAL_RE.search(text))


def build_query(prompt: str, max_len: int = 200) -> str:
    q = re.sub(r"\s+", " ", (prompt or "").strip())[:max_len]
    q = re.sub(
        r"^(montre-moi|montre|illustre|trouve|trouvez|comment trouver|o\xf9 trouver)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    )
    return q.strip() or (prompt or "").strip()[:max_len]


def images_enabled() -> bool:
    provider = getattr(settings, "JEJEGPT_IMAGE_SEARCH_PROVIDER", "duckduckgo")
    if provider == "off":
        return False
    if not getattr(settings, "JEJEGPT_IMAGE_SEARCH_AUTO_ENABLED", True):
        return False
    if provider == "brave" and not getattr(settings, "JEJEGPT_IMAGE_SEARCH_API_KEY", ""):
        return False
    return True


def _dedup(results: list[ImageResult]) -> list[ImageResult]:
    seen: set[str] = set()
    out: list[ImageResult] = []
    for r in results:
        if not _valid_http_url(r.image_url):
            continue
        if r.image_url in seen:
            continue
        seen.add(r.image_url)
        out.append(r)
    return out


def _search_brave(query: str, count: int, timeout: float) -> list[ImageResult]:
    key = getattr(settings, "JEJEGPT_IMAGE_SEARCH_API_KEY", "")
    if not key:
        return []
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/images/search",
        params={"q": query, "count": min(count, 10)},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    out: list[ImageResult] = []
    for item in (resp.json().get("results") or [])[:count]:
        src = item.get("src") or (item.get("thumbnail") or {}).get("src") or ""
        page = item.get("url") or item.get("page_url") or src
        if not _valid_http_url(src):
            continue
        out.append(
            ImageResult(
                title=(item.get("title") or query)[:160],
                image_url=src,
                source_url=page if _valid_http_url(page) else src,
                source_name=(item.get("source") or "")[:80],
                thumb_url=(item.get("thumbnail") or {}).get("src", ""),
            )
        )
    return out


def _search_openverse(query: str, count: int, timeout: float) -> list[ImageResult]:
    resp = httpx.get(
        "https://api.openverse.org/v1/images/",
        params={"q": query, "page_size": min(count, 20)},
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    out: list[ImageResult] = []
    for item in (resp.json().get("results") or [])[:count]:
        src = item.get("url") or ""
        if not _valid_http_url(src):
            continue
        out.append(
            ImageResult(
                title=(item.get("title") or query)[:160],
                image_url=src,
                source_url=item.get("foreign_landing_url") or src,
                source_name=(item.get("creator") or "")[:80],
                thumb_url=item.get("thumbnail") or "",
            )
        )
    return out


def _search_duckduckgo(query: str, count: int, timeout: float) -> list[ImageResult]:
    with httpx.Client(
        timeout=timeout, headers={"User-Agent": "Mozilla/5.0 JejeGPT"}, follow_redirects=True
    ) as client:
        home = client.get("https://duckduckgo.com/?q=" + quote_plus(query) + "&iar=images")
        home.raise_for_status()
        m = re.search(r"vqd=([\d-]+)", home.text) or re.search(
            r"vqd['\"]?\s*[:=]\s*['\"]([\d-]+)['\"]", home.text
        )
        if not m:
            logger.warning("image search duckduckgo: token vqd introuvable (page %d signes)", len(home.text))
            return []
        vqd = m.group(1)
        resp = client.get(
            "https://duckduckgo.com/i.js",
            params={"l": "fr-fr", "o": "json", "q": query, "vqd": vqd},
        )
        resp.raise_for_status()
        out: list[ImageResult] = []
        for item in (resp.json().get("results") or [])[:count]:
            src = item.get("image") or item.get("thumbnail") or ""
            page = item.get("url") or src
            if not _valid_http_url(src):
                continue
            out.append(
                ImageResult(
                    title=(item.get("title") or query)[:160],
                    image_url=src,
                    source_url=page if _valid_http_url(page) else src,
                    source_name="DuckDuckGo",
                    thumb_url=item.get("thumbnail") or "",
                )
            )
        return out


def _search_with(provider: str, query: str, count: int, timeout: float) -> list[ImageResult]:
    if provider == "brave":
        return _search_brave(query, count, timeout)
    if provider == "openverse":
        return _search_openverse(query, count, timeout)
    return _search_duckduckgo(query, count, timeout)


def search_images(query: str, count: int = 0) -> list[ImageResult]:
    """Recherche best-effort avec fallback. Ne lève jamais : retourne [] en échec."""
    provider = getattr(settings, "JEJEGPT_IMAGE_SEARCH_PROVIDER", "duckduckgo")
    if provider == "off":
        return []
    max_images = count or getattr(settings, "JEJEGPT_IMAGE_SEARCH_MAX_IMAGES", 3)
    max_images = max(1, min(int(max_images), 6))
    q = build_query(query)
    if not q:
        return []
    cached = _CACHE.get(f"{provider}:{q}:{max_images}")
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
        return list(cached[1])
    timeout = float(getattr(settings, "JEJEGPT_IMAGE_SEARCH_TIMEOUT", 5))
    chain = [provider] + [p for p in ("duckduckgo", "openverse") if p != provider]
    if provider == "brave" and not getattr(settings, "JEJEGPT_IMAGE_SEARCH_API_KEY", ""):
        logger.warning("image search brave: pas de clé API, repli sur %s", chain[1])
        chain = chain[1:]
    for name in chain:
        try:
            results = _dedup(_search_with(name, q, max_images, timeout))[:max_images]
        except Exception:
            logger.warning("image search failed (provider=%s)", name, exc_info=True)
            continue
        if results:
            _CACHE[f"{provider}:{q}:{max_images}"] = (time.monotonic(), list(results))
            return results
        logger.warning("image search vide (provider=%s, query=%r)", name, q[:80])
    return []


def images_markdown(results: list[ImageResult]) -> str:
    """Bloc Markdown galerie + sources, prêt pour render_markdown()."""
    if not results:
        return ""
    lines = ["", "🖼️ **Images**", ""]
    for r in results:
        alt = (r.title or "illustration").replace("[", "").replace("]", "")[:120]
        src = r.thumb_url or r.image_url
        lines.append(f"[![{alt}]({src})]({r.source_url})")
    lines.append("")
    sources = " · ".join(
        f"[{(r.source_name or 'source')[:40]}]({r.source_url})" for r in results
    )
    lines.append(f"<sub>Sources images : {sources}</sub>")
    return "\n".join(lines)
