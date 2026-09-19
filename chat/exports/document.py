"""Modèle de document minimal parsé depuis du Markdown (titres, paragraphes, listes, tableaux, code)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RE_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_RE_BULLET = re.compile(r"^[-*]\s+(.*\S)\s*$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?[\s:|-]*\s*$")
_RE_FENCE = re.compile(r"^```(\w*)\s*$")


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
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class CodeBlock:
    language: str
    code: str


Block = Heading | Paragraph | BulletList | Table | CodeBlock


def _split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def parse_markdown(text: str) -> list[Block]:
    """Parse déterministe et sans HTML : le texte reste littéral (pas d'interprétation)."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    bullets: list[str] = []
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

    lines = (text or "").replace("\r\n", "\n").split("\n")
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
                flush_bullets()
                in_code = True
                code_lang = fence.group(1) or "code"
            i += 1
            continue
        if in_code:
            code_lines.append(line.rstrip())
            i += 1
            continue
        heading = _RE_HEADING.match(stripped)
        if heading:
            flush_paragraph()
            flush_bullets()
            blocks.append(Heading(level=len(heading.group(1)), text=heading.group(2)))
            i += 1
            continue
        bullet = _RE_BULLET.match(stripped)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1))
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _RE_TABLE_SEP.match(lines[i + 1].strip()):
            flush_paragraph()
            flush_bullets()
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
        if not stripped:
            flush_paragraph()
            flush_bullets()
            i += 1
            continue
        paragraph.append(stripped)
        i += 1
    flush_paragraph()
    flush_bullets()
    if in_code:
        blocks.append(CodeBlock(language=code_lang, code="\n".join(code_lines)))
    return blocks


def sanitize_cell(value: str) -> str:
    """Neutralise l'injection de formules Excel : = + - @ et tab/CR en tête deviennent littéraux."""
    text = value or ""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text
