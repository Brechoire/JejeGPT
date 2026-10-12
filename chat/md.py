"""Rendu Markdown server-side + sanitisation."""
import html as html_lib
import re
import unicodedata
from urllib.parse import urlparse

import bleach
import markdown

ALLOWED_TAGS = [
    *bleach.sanitizer.ALLOWED_TAGS,
    "pre", "code", "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "hr", "img",
    "div", "button", "span", "figure", "figcaption", "sub",  # barre .codeblock + galerie .image-grid
    "details", "summary",  # sommaire auto des longues réponses
    "br",  # sauts de ligne simples (extension nl2br) : paragraphes aérés type mail
    "p",  # paragraphes Markdown : sans lui, bleach les dépouille et tout se colle
]
ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "target", "rel", "data-tip", "aria-label"],
    "code": ["class"],
    "pre": ["class"],
    "img": ["src", "alt", "title", "loading", "width", "height", "referrerpolicy"],
    "div": ["class", "hidden"],
    "button": ["class", "onclick", "title"],
    "span": ["class"],
    "figure": ["class"],
    "figcaption": ["class"],
    "h1": ["id"],
    "h2": ["id"],
    "h3": ["id"],
    "h4": ["id"],
    "details": ["class"],
    "ul": ["class"],
    "li": ["class"],
    "p": ["class"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_RE_PRE = re.compile(r"<pre><code(?: class=\"([^\"]*)\")?>(.*?)</code></pre>", re.DOTALL)
_RE_FENCED = re.compile(r"```.*?```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
_RE_URL = re.compile(r"https?://[^\s<>\]\)]+")
# Fin de phrase collée à la suivante (ex. "sulfure.Je") : le modèle oublie
# parfois l'espace. Le lookbehind minuscule évite "U.S.A", versions, etc.
_RE_SENTENCE_DOT = re.compile(r"(?<=[a-zàâäéèêëîïôöùûüÿç0-9\)»\"'])\.([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ])")
_RE_SENTENCE_MARK = re.compile(r"(?<=[a-zàâäéèêëîïôöùûüÿç0-9\)»\"'])([!?…])([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ])")
# Titre Markdown collé au texte (ex. "illustrer.## 🌊") : doit repartir à la ligne.
_RE_GLUED_HEADING = re.compile(r"([^\n])\s*(#{2,4} )")
_RE_TABLE = re.compile(r"<table>(.*?)</table>", re.DOTALL)
_SEP = r"(?:\s|<br\s*/?>)*"
_RE_IMG_P = re.compile(
    rf"<p>({_SEP}(?:<a[^>]*>{_SEP}<img[^>]*>{_SEP}</a>{_SEP}|<img[^>]*>{_SEP}){{1,6}})</p>",
    re.DOTALL,
)
_RE_IMG_TAG = re.compile(r"<img(?![^>]*\bloading=)", re.IGNORECASE)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _wrap_codeblocks(html: str) -> str:
    """Enveloppe chaque <pre> : barre langage + boutons Copier / Preview / Télécharger.

    Preview/Télécharger uniquement pour html, svg, mermaid (artifacts) : le rendu
    est construit côté client (iframe sandbox / mermaid.js), jamais de HTML
    injecté server-side — pas de surface XSS supplémentaire.
    """

    PREVIEW_LANGS = {"html", "svg", "mermaid", "xml"}

    def _repl(match: re.Match) -> str:
        classes = match.group(1) or ""
        lang = classes.replace("language-", "").split()[0] if classes else "code"
        code = match.group(2)
        extra = ""
        if lang.lower() in PREVIEW_LANGS:
            extra = (
                "<button type=\"button\" onclick=\"previewArtifact(this)\" "
                "title=\"Aperçu rendu (sandbox local)\">Aperçu</button>"
                "<button type=\"button\" onclick=\"downloadArtifact(this)\" "
                "title=\"Télécharger ce bloc\">↓</button>"
            )
        bar = (
            f"<div class=\"codeblock\"><div class=\"codeblock-bar\">"
            f"<span class=\"lang\">{html_lib.escape(lang)}</span>"
            f"<button type=\"button\" onclick=\"copyCode(this)\">Copier</button>"
            f"{extra}"
            f"</div><pre><code class=\"{html_lib.escape(classes)}\">{code}</code></pre>"
            f"<div class=\"artifact-preview\" hidden=\"hidden\"></div></div>"
        )
        return bar

    return _RE_PRE.sub(_repl, html)


def _wrap_tables(html: str) -> str:
    """Enveloppe chaque <table> : barre (compteur + Copier CSV) + zone scroll.

    Carte blanche arrondie (voir jejegpt.css) ; la barre reste fixe pendant
    le scroll horizontal/vertical du tableau.
    """

    def _repl(match: re.Match) -> str:
        inner = match.group(1)
        cols = len(re.findall(r"<th[ >]", inner))
        body = re.search(r"<tbody>(.*?)</tbody>", inner, re.DOTALL)
        rows = len(re.findall(r"<tr[ >]", body.group(1))) if body else 0
        meta = (
            f"{rows} ligne{'s' if rows > 1 else ''}"
            f" × {cols} colonne{'s' if cols > 1 else ''}"
        )
        return (
            f"<div class=\"table-wrap\"><div class=\"table-bar\">"
            f"<span class=\"table-meta\">{meta}</span>"
            f"<button type=\"button\" onclick=\"copyTableCSV(this)\" "
            f"title=\"Copier le tableau en CSV (séparateur point-virgule)\">Copier CSV</button>"
            f"</div><div class=\"table-scroll\"><table>{inner}</table></div></div>"
        )

    return _RE_TABLE.sub(_repl, html)


def _slugify_heading(text: str) -> str:
    """Slug d'ancre stable, sans accents : 'Résumé Livres' → 'resume-livres'."""
    plain = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
    ascii_ = "".join(
        c for c in unicodedata.normalize("NFD", plain)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


_RE_HEADING = re.compile(r"<h([234])>(.*?)</h\1>", re.DOTALL)


def _add_heading_ids_and_toc(html: str) -> str:
    """Ajoute un id à chaque h2-h4 + sommaire <details> si ≥ 3 sections.

    Appelé sur le HTML Markdown brut (le code est déjà échappé, donc la
    regex ne touche pas aux exemples de code).
    """
    seen: dict[str, int] = {}
    headings: list[tuple[int, str, str]] = []
    out: list[str] = []
    pos = 0
    for match in _RE_HEADING.finditer(html):
        out.append(html[pos:match.start()])
        slug = _slugify_heading(match.group(2)) or f"section-{len(headings) + 1}"
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 1
        level = int(match.group(1))
        label = html_lib.escape(html_lib.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip())
        headings.append((level, slug, label))
        out.append(f"<h{level} id=\"{slug}\">{match.group(2)}</h{level}>")
        pos = match.end()
    out.append(html[pos:])
    if len(headings) < 3:
        return "".join(out)
    items = "".join(
        f"<li class=\"toc-h{level}\"><a href=\"#{slug}\">{label}</a></li>"
        for level, slug, label in headings
    )
    toc = (
        f"<details class=\"toc\"><summary>Sommaire</summary>"
        f"<ul class=\"toc-list\">{items}</ul></details>"
    )
    return toc + "".join(out)


def _wrap_images(html: str) -> str:
    """Regroupe les images adjacentes en galerie (voir .image-grid).

    Ajoute lazy-load + no-referrer sur chaque <img> pour limiter
    les fuites vers les hébergeurs hotlinkés.
    """

    def _repl(match: re.Match) -> str:
        inner = _RE_BR.sub("", match.group(1))
        inner = _RE_IMG_TAG.sub('<img loading="lazy" referrerpolicy="no-referrer"', inner)
        return f'<figure class="image-grid">{inner}</figure>'

    return _RE_IMG_P.sub(_repl, html)


def _linkify_callback(attrs, new=False):
    """Ouvre les liens externes dans un nouvel onglet, sans fuite referrer/opener.

    Les ancres internes du sommaire (href="#…") restent sur la page.
    """
    href = attrs.get((None, "href"), "")
    if href.startswith("#"):
        attrs.pop((None, "target"), None)
        attrs.pop((None, "rel"), None)
        return attrs
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer nofollow"
    return attrs


def normalize_text(text: str) -> str:
    """Répare la ponctuation collée du modèle, sans toucher au code ni aux URL.

    - "sulfure.Je" → "sulfure. Je" (point / ! / ? / … suivi d'une majuscule)
    - "illustrer.## Titre" → "illustrer.\\n\\n## Titre" (titre repart à la ligne)
    Protège les blocs de code, le code inline et les URL via placeholders.
    """
    if not text:
        return text
    saved: list[str] = []

    def _save(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    text = _RE_FENCED.sub(_save, text)
    text = _RE_INLINE_CODE.sub(_save, text)
    text = _RE_URL.sub(_save, text)
    text = _RE_SENTENCE_DOT.sub(r". \1", text)
    text = _RE_SENTENCE_MARK.sub(r"\1 \2", text)
    text = _RE_GLUED_HEADING.sub(r"\1\n\n\2", text)

    def _restore(match: re.Match) -> str:
        return saved[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, text)


_RE_ANCHOR = re.compile(
    r"<a\s[^>]*href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_RE_TAG = re.compile(r"<[^>]+>")
_ARTICLES_RE = re.compile(r"^(?:le|la|les|l'|de|des|du|d'|the)\s+", re.IGNORECASE)
_SRC_MAX = 10
_SRC_COLORS = 8


def _site_bubbles(anchors: list[tuple[str, str]]) -> list[dict]:
    """Déduplique par domaine : une bulle par site (lettre + nom + url)."""
    seen: dict[str, dict] = {}
    for url, text in anchors:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            continue
        label = _RE_TAG.sub("", html_lib.unescape(text)).strip()
        if not label or re.match(r"^https?://", label):
            label = host
        if host in seen:
            continue
        letter = "•"
        short = _ARTICLES_RE.sub("", label).strip()
        for char in short:
            if char.isalnum():
                letter = char.upper()
                break
        seen[host] = {
            "url": url, "label": label, "letter": letter,
            "color": sum(ord(c) for c in host) % _SRC_COLORS,
        }
    return list(seen.values())


def _bubbles_html(sites: list[dict]) -> str:
    """Groupe d'avatars-lettre chevauchants, tooltip = nom du site."""
    shown, extra = sites[:_SRC_MAX], len(sites) - _SRC_MAX
    parts = [
        f"<a class=\"src-bubble src-b{s['color']}\" href=\"{html_lib.escape(s['url'], quote=True)}\" "
        f"target=\"_blank\" rel=\"noopener noreferrer nofollow\" "
        f"data-tip=\"{html_lib.escape(s['label'], quote=True)}\" "
        f"aria-label=\"Source : {html_lib.escape(s['label'], quote=True)}\" "
        f"title=\"{html_lib.escape(s['label'], quote=True)}\">{html_lib.escape(s['letter'])}</a>"
        for s in shown
    ]
    if extra > 0:
        parts.append(f"<span class=\"src-bubble src-more\" title=\"{extra} autres sources\">+{extra}</span>")
    return "<div class=\"src-group\" aria-label=\"Sources\">" + "".join(parts) + "</div>"


def _append_sources_footer(html: str) -> str:
    """Pied discret 'Sources : n lien(s)' + bulles-lettre par site (tooltip = nom).

    Compté au rendu (pas en base) ; les liens déjà visibles restent inchangés.
    """
    if not html:
        return html
    anchors = _RE_ANCHOR.findall(html)
    if not anchors:
        return html
    count = len(anchors)
    sites = _site_bubbles(anchors)
    return html + (
        f"<p class=\"sources-note\">Sources : {count} lien{'s' if count > 1 else ''}</p>"
        + _bubbles_html(sites)
    )


def render_markdown(text: str) -> str:
    # Pas de codehilite server-side : fenced_code émet <code class="language-x">,
    # la coloration est faite côté client par highlight.js (déjà chargé).
    html = markdown.markdown(normalize_text(text or ""), extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
    html = _add_heading_ids_and_toc(html)
    html = _wrap_codeblocks(html)
    html = _wrap_tables(html)
    html = _wrap_images(html)
    cleaned = bleach.clean(
        html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS, strip=True,
    )
    # Rend les URL brutes (https://…) cliquables ; ignore <pre>/<code>.
    linked = bleach.linkify(
        cleaned,
        callbacks=[_linkify_callback],
        skip_tags=["pre", "code"],
        parse_email=False,
    )
    return _append_sources_footer(linked)
