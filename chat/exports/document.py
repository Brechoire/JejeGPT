"""Modèle de document parsé depuis du Markdown (titres, paragraphes, listes, tableaux, code).

Le texte des blocs conserve la syntaxe inline (``**gras**``, ``*italique*``,
`` `code` ``, ``[label](url)``) ; ce sont les renderers (PDF/DOCX/XLSX/PPTX)
qui l'interprètent via ``strip_inline`` / ``inline_segments``.
Les blocs de code restent verbatim (jamais interprétés).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RE_HEADING = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
_RE_BULLET = re.compile(r"^[-*+]\s+(.*\S)\s*$")
_RE_ORDERED = re.compile(r"^(\d+)[.)]\s+(.*\S)\s*$")
_RE_TASK = re.compile(r"^\[(?:\s|x|X)\]\s*(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^\s*(?:---+\s*|\*\*\*+\s*|___+\s*|(?:-\s+){3,}|(?:\*\s+){3,})\s*$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?[\s:|-]*\s*$")
_RE_FENCE = re.compile(r"^```(\w*)\s*$")

# --- Inline ---------------------------------------------------------------

_RE_INLINE_CODE = re.compile(r"`([^`\n]+?)`")
_RE_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s\n]+)(?:\s+\"[^\"]*\")?\)")
_RE_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+|mailto:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_RE_AUTOLINK = re.compile(r"<(https?://[^<>\s]+)>")
_RE_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_RE_STRIKE = re.compile(r"~~(.+?)~~")
_RE_ITALIC_STAR = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_RE_ITALIC_US = re.compile(r"(?<!\w)_(.+?)_(?!\w)")


@dataclass
class Segment:
    """Un morceau de texte inline avec son style."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link: str | None = None


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class BulletList:
    items: list[str] = field(default_factory=list)


@dataclass
class OrderedList:
    items: list[str] = field(default_factory=list)


@dataclass
class Quote:
    text: str


@dataclass
class HorizontalRule:
    pass


@dataclass
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class CodeBlock:
    language: str
    code: str


Block = Heading | Paragraph | BulletList | OrderedList | Quote | HorizontalRule | Table | CodeBlock


def _split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _parse_rich(text: str) -> list[Segment]:
    """Découpe le texte inline en segments stylés (code protégé en premier)."""
    code_spans: list[str] = []

    def _save_code(m: re.Match) -> str:
        code_spans.append(m.group(1))
        return f"\x00{code_spans.__len__() - 1}\x00"

    working = _RE_INLINE_CODE.sub(_save_code, text or "")

    segments: list[Segment] = []
    # Découpe liens/images/autolinks en tokens, puis bold/strike/italic par token.
    token_re = re.compile(
        r"(!\[[^\]\n]*\]\((?:[^)\s\n]+)(?:\s+\"[^\"]*\")?\)"
        r"|\[[^\]\n]+\]\((?:https?://[^)\s]+|mailto:[^)\s]+)(?:\s+\"[^\"]*\")?\)"
        r"|<(?:https?://[^<>\s]+)>"
        r"|\x00\d+\x00)"
    )
    pos = 0
    for m in token_re.finditer(working):
        if m.start() > pos:
            segments.extend(_parse_emphasis(working[pos : m.start()]))
        token = m.group(0)
        img = _RE_IMAGE.fullmatch(token)
        if img:
            segments.append(Segment(text=img.group(1)))
            pos = m.end()
            continue
        link = _RE_LINK.fullmatch(token)
        if link:
            for seg in _parse_emphasis(link.group(1)):
                seg.link = link.group(2)
                segments.append(seg)
            pos = m.end()
            continue
        auto = _RE_AUTOLINK.fullmatch(token)
        if auto:
            segments.append(Segment(text=auto.group(1), link=auto.group(1)))
            pos = m.end()
            continue
        code_idx = re.fullmatch(r"\x00(\d+)\x00", token)
        if code_idx:
            segments.append(Segment(text=code_spans[int(code_idx.group(1))], code=True))
            pos = m.end()
            continue
        segments.extend(_parse_emphasis(token))
        pos = m.end()
    if pos < len(working):
        segments.extend(_parse_emphasis(working[pos:]))
    # Restaure les placeholders code restants (cas limites) en texte brut.
    for seg in segments:
        if not seg.code and "\x00" in seg.text:
            def _restore(mm: re.Match) -> str:
                try:
                    return code_spans[int(mm.group(1))]
                except (IndexError, ValueError):
                    return mm.group(0)

            seg.text = re.sub(r"\x00(\d+)\x00", _restore, seg.text)
    return [s for s in segments if s.text]


def _parse_emphasis(text: str) -> list[Segment]:
    """Parse bold/strike/italic sur un fragment sans lien ni code."""
    parts: list[tuple[str, bool, bool, bool]] = [(text, False, False, False)]

    def _apply(pattern: re.Pattern, flag: str) -> None:
        nonlocal parts
        out: list[tuple[str, bool, bool, bool]] = []
        for chunk, b, i, s in parts:
            last = 0
            matched = False
            for m in pattern.finditer(chunk):
                matched = True
                if m.start() > last:
                    out.append((chunk[last : m.start()], b, i, s))
                nb, ni, ns = b, i, s
                if flag == "bold":
                    nb = True
                elif flag == "italic":
                    ni = True
                else:
                    ns = True
                out.append((m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1), nb, ni, ns))
                last = m.end()
            if not matched:
                out.append((chunk, b, i, s))
            elif last < len(chunk):
                out.append((chunk[last:], b, i, s))
        parts = out

    _apply(_RE_BOLD, "bold")
    _apply(_RE_STRIKE, "strike")
    _apply(_RE_ITALIC_STAR, "italic")
    _apply(_RE_ITALIC_US, "italic")
    return [Segment(text=c, bold=b, italic=i, strike=s) for c, b, i, s in parts if c]


def inline_segments(text: str) -> list[Segment]:
    """Segments inline stylés pour les renderers riches (PDF/DOCX)."""
    return _parse_rich(text)


def strip_inline(text: str) -> str:
    """Texte net sans syntaxe Markdown (XLSX/PPTX, cellules, titres)."""
    return "".join(seg.text for seg in _parse_rich(text or ""))


def _normalize(text: str) -> str:
    try:
        from chat.md import normalize_text  # import local : évite le cycle au chargement
    except Exception:
        return text or ""
    try:
        return normalize_text(text or "")
    except Exception:
        return text or ""


def parse_markdown(text: str) -> list[Block]:
    """Parse déterministe et sans HTML : la syntaxe block est interprétée.

    L'inline (``**``/``*``/`` ` ``/``[]()``) est conservée dans ``text`` et
    doit être résolue par ``strip_inline`` / ``inline_segments`` côté renderer.
    Les blocs de code restent verbatim.
    """
    blocks: list[Block] = []
    paragraph: list[str] = []
    bullets: list[str] = []
    ordered: list[str] = []
    quotes: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Paragraph(text=" ".join(paragraph).strip()))
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            blocks.append(BulletList(items=list(bullets)))
            bullets.clear()

    def flush_ordered() -> None:
        if ordered:
            blocks.append(OrderedList(items=list(ordered)))
            ordered.clear()

    def flush_quotes() -> None:
        if quotes:
            blocks.append(Quote(text=" ".join(quotes).strip()))
            quotes.clear()

    def flush_lists() -> None:
        flush_bullets()
        flush_ordered()

    lines = _normalize(text).replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        fence = _RE_FENCE.match(stripped)
        if fence:
            if in_code:
                blocks.append(CodeBlock(language=code_lang, code="\n".join(code_lines)))
                code_lines.clear()
                in_code = False
            else:
                flush_paragraph()
                flush_lists()
                flush_quotes()
                in_code = True
                code_lang = fence.group(1) or "code"
            i += 1
            continue
        if in_code:
            code_lines.append(line.rstrip())
            i += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_lists()
            flush_quotes()
            i += 1
            continue
        if _RE_HR.match(stripped):
            flush_paragraph()
            flush_lists()
            flush_quotes()
            blocks.append(HorizontalRule())
            i += 1
            continue
        heading = _RE_HEADING.match(stripped)
        if heading:
            flush_paragraph()
            flush_lists()
            flush_quotes()
            blocks.append(Heading(level=min(len(heading.group(1)), 6), text=heading.group(2).strip()))
            i += 1
            continue
        quote = _RE_QUOTE.match(stripped)
        if quote:
            flush_paragraph()
            flush_lists()
            quotes.append(quote.group(1).strip())
            i += 1
            continue
        bullet = _RE_BULLET.match(stripped)
        if bullet:
            flush_paragraph()
            flush_ordered()
            flush_quotes()
            content = _RE_TASK.sub(r"\1", bullet.group(1).strip())
            bullets.append(content)
            i += 1
            continue
        order_m = _RE_ORDERED.match(stripped)
        if order_m:
            flush_paragraph()
            flush_bullets()
            flush_quotes()
            content = _RE_TASK.sub(r"\1", order_m.group(2).strip())
            ordered.append(content)
            i += 1
            continue
        # Ligne indentée sous une liste : continuation du dernier item.
        if line[:1] in (" ", "\t") and stripped and (bullets or ordered):
            target = bullets if bullets else ordered
            target[-1] = f"{target[-1]} {stripped}"
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _RE_TABLE_SEP.match(lines[i + 1].strip()):
            flush_paragraph()
            flush_lists()
            flush_quotes()
            headers = _split_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i]:
                row_line = lines[i].strip()
                if not row_line:
                    break
                rows.append(_split_row(row_line))
                i += 1
            blocks.append(Table(headers=headers, rows=rows))
            continue
        flush_lists()
        flush_quotes()
        paragraph.append(stripped)
        i += 1
    flush_paragraph()
    flush_lists()
    flush_quotes()
    if in_code:
        blocks.append(CodeBlock(language=code_lang, code="\n".join(code_lines)))
    return blocks


def sanitize_cell(value: str) -> str:
    """Neutralise l'injection de formules Excel : = + - @ et tab/CR en tête deviennent littéraux."""
    text = value or ""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text
