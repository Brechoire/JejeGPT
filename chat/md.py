"""Rendu Markdown server-side + sanitisation."""
import html as html_lib
import re

import bleach
import markdown

ALLOWED_TAGS = [
    *bleach.sanitizer.ALLOWED_TAGS,
    "pre", "code", "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "hr", "img",
    "div", "button", "span",  # barre .codeblock générée server-side (onclick statique)
]
ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "target", "rel"],
    "code": ["class"],
    "pre": ["class"],
    "img": ["src", "alt"],
    "div": ["class"],
    "button": ["class", "onclick"],
    "span": ["class"],
}

_RE_PRE = re.compile(r"<pre><code(?: class=\"([^\"]*)\")?>(.*?)</code></pre>", re.DOTALL)
_RE_TABLE = re.compile(r"<table>(.*?)</table>", re.DOTALL)


def _wrap_codeblocks(html: str) -> str:
    """Enveloppe chaque <pre> : barre langage + bouton Copier (voir jejegpt.css)."""

    def _repl(match: re.Match) -> str:
        classes = match.group(1) or ""
        lang = classes.replace("language-", "").split()[0] if classes else "code"
        code = match.group(2)
        bar = (
            f"<div class=\"codeblock\"><div class=\"codeblock-bar\">"
            f"<span class=\"lang\">{html_lib.escape(lang)}</span>"
            f"<button type=\"button\" onclick=\"copyCode(this)\">Copier</button>"
            f"</div><pre><code class=\"{html_lib.escape(classes)}\">{code}</code></pre></div>"
        )
        return bar

    return _RE_PRE.sub(_repl, html)


def _wrap_tables(html: str) -> str:
    """Enveloppe chaque <table> : carte blanche arrondie (voir jejegpt.css)."""

    def _repl(match: re.Match) -> str:
        return f"<div class=\"table-wrap\"><table>{match.group(1)}</table></div>"

    return _RE_TABLE.sub(_repl, html)


def _linkify_callback(attrs, new=False):
    """Ouvre les liens dans un nouvel onglet, sans fuite referrer/opener."""
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer nofollow"
    return attrs


def render_markdown(text: str) -> str:
    # Pas de codehilite server-side : fenced_code émet <code class="language-x">,
    # la coloration est faite côté client par highlight.js (déjà chargé).
    html = markdown.markdown(text or "", extensions=["fenced_code", "tables", "sane_lists"])
    html = _wrap_codeblocks(html)
    html = _wrap_tables(html)
    cleaned = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    # Rend les URL brutes (https://…) cliquables ; ignore <pre>/<code>.
    return bleach.linkify(
        cleaned,
        callbacks=[_linkify_callback],
        skip_tags=["pre", "code"],
        parse_email=False,
    )
