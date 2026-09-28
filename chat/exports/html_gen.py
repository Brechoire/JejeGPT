"""Générateur HTML e-mail-safe (mise en page tableaux + <style> dans <head>)."""
from __future__ import annotations

import html as html_lib

from ..md import render_markdown
from .document import strip_inline

_EMAIL_CSS = (
    "body{font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#0d0d0d}"
    "h1{font-size:24px}h2{font-size:20px;color:#0d0d0d}h3{font-size:17px}"
    "a{color:#0b5fff}table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #d4d4d4;padding:8px 12px;text-align:left;vertical-align:top}"
    "th{background:#f4f4f4}blockquote{border-left:3px solid #d4d4d4;margin:8px 0;padding-left:12px;color:#424242}"
    "code{font-size:13px}.toc{background:#fafafa;border:1px solid #d4d4d4;border-radius:8px;padding:8px 14px}"
)


def build_html(title: str, markdown_text: str) -> bytes:
    """Document HTML complet d'une newsletter (partie Markdown avant ---TEXTE---)."""
    from ..newsletter import split_newsletter

    _subject, md_part, _text = split_newsletter(markdown_text)
    heading = html_lib.escape(strip_inline((title or "").strip()) or "Newsletter JejeGPT")
    body = render_markdown(md_part)
    doc = (
        "<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
        f"<title>{heading}</title><style>{_EMAIL_CSS}</style></head>"
        f"<body><h1>{heading}</h1>{body}</body></html>"
    )
    return doc.encode("utf-8")
