"""Générateur PDF paginé via ReportLab Platypus (garde, titres, tableaux, code, pagination)."""
from __future__ import annotations

import html as html_lib
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, Preformatted, Spacer, Table, TableStyle

from .document import Block, BulletList, CodeBlock, Heading, parse_markdown
from .document import Paragraph as DocParagraph
from .document import Table as DocTable


def _escape(text: str) -> str:
    return html_lib.escape(text or "").replace("\n", "<br/>")


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"JejeGPT — page {doc.page}")
    canvas.restoreState()


def build_pdf(title: str, markdown_text: str) -> bytes:
    from reportlab.platypus import SimpleDocTemplate

    blocks: list[Block] = parse_markdown(markdown_text)
    if not title.strip() and not blocks:
        raise ValueError("Contenu vide.")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(_escape(title.strip() or "Export JejeGPT"), styles["Title"]),
        Paragraph(_escape("Généré par JejeGPT — usage local"), styles["Normal"]),
        Spacer(1, 0.6 * cm),
    ]
    for block in blocks:
        if isinstance(block, Heading):
            key = "Heading1" if block.level == 1 else "Heading2" if block.level == 2 else "Heading3"
            story.append(Paragraph(_escape(block.text), styles[key]))
        elif isinstance(block, DocParagraph):
            story.append(Paragraph(_escape(block.text), styles["Normal"]))
            story.append(Spacer(1, 0.25 * cm))
        elif isinstance(block, BulletList):
            for item in block.items:
                story.append(Paragraph("• " + _escape(item), styles["Normal"]))
            story.append(Spacer(1, 0.25 * cm))
        elif isinstance(block, DocTable):
            data = [[_escape(c) for c in block.headers]]
            data += [[_escape(c) for c in row] for row in block.rows]
            width = max(len(block.headers), 1)
            cells = [[Paragraph(cell, styles["Normal"]) for cell in row] for row in data]
            table = Table(cells, colWidths=[(16 * cm) / width] * width)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0.4, 0.4, 0.4))]))
            story.append(table)
            story.append(Spacer(1, 0.35 * cm))
        elif isinstance(block, CodeBlock):
            story.append(Preformatted(f"[{block.language}]\n{block.code}", styles["Code"]))
            story.append(Spacer(1, 0.25 * cm))
    story.append(PageBreak())
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
