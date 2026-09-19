"""Générateur DOCX structuré (titres, paragraphes, listes, tableaux, code, pied de page)."""
from __future__ import annotations

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from .document import Block, BulletList, CodeBlock, Heading, Paragraph, Table, parse_markdown


def build_docx(title: str, markdown_text: str) -> bytes:
    blocks: list[Block] = parse_markdown(markdown_text)
    if not title.strip() and not blocks:
        raise ValueError("Contenu vide.")
    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(11)
    doc.add_heading(title.strip() or "Export JejeGPT", level=0)
    for block in blocks:
        if isinstance(block, Heading):
            doc.add_heading(block.text, level=min(block.level, 3))
        elif isinstance(block, Paragraph):
            doc.add_paragraph(block.text)
        elif isinstance(block, BulletList):
            for item in block.items:
                doc.add_paragraph(item, style="List Bullet")
        elif isinstance(block, Table):
            rows = [block.headers, *block.rows]
            cols = max(len(r) for r in rows)
            table = doc.add_table(rows=len(rows), cols=cols)
            table.style = "Light Grid Accent 1"
            for ri, row in enumerate(rows):
                for ci in range(cols):
                    table.cell(ri, ci).text = row[ci] if ci < len(row) else ""
        elif isinstance(block, CodeBlock):
            para = doc.add_paragraph()
            run = para.add_run(block.code)
            run.font.size = Pt(9)
            run.font.name = "Consolas"
    section = doc.sections[0]
    footer = section.footer.paragraphs[0]
    footer.text = "Généré par JejeGPT — usage local"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
