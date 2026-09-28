"""Générateur .eml multipart (texte + HTML), ouvrable en brouillon Outlook."""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate


def build_eml(title: str, markdown_text: str) -> bytes:
    """Construit un message .eml : objet + texte brut + HTML e-mail-safe."""
    from ..newsletter import split_newsletter
    from .html_gen import build_html

    subject, _md_part, text_part = split_newsletter(markdown_text)
    msg = EmailMessage()
    msg["Subject"] = subject or (title or "Newsletter JejeGPT")[:80]
    msg["From"] = "JejeGPT <newsletter@localhost>"
    msg["To"] = "mediatheque@localhost"
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_part or subject)
    msg.add_alternative(build_html(title, markdown_text).decode("utf-8"), subtype="html")
    return msg.as_bytes()
