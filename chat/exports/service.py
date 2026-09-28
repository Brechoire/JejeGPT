"""Point d'entrée unique : valide, génère (BytesIO) puis persiste un Export lié à un Message."""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from ..models import Export, Message
from .core import check_quota, check_size, safe_name
from .docx_gen import build_docx
from .eml_gen import build_eml
from .html_gen import build_html
from .pdf_gen import build_pdf
from .pptx_gen import build_pptx
from .xlsx_gen import build_xlsx

_BUILDERS = {
    "pdf": build_pdf,
    "docx": build_docx,
    "xlsx": build_xlsx,
    "pptx": build_pptx,
    "html": build_html,
    "eml": build_eml,
}

FORMATS = tuple(_BUILDERS)


EXPORT_KEYWORDS = (
    "génère", "genere", "générer", "generer",
    "crée", "cree", "créer", "creer",
    "fais", "faire", "prépare", "prepare",
    "sors", "donne", "rédige", "redige",
    "fichier", "export", "télécharge", "telecharge",
    "document", "pièce jointe", "piece jointe",
)


def detect_export_request(text: str) -> str | None:
    """Détecte une demande explicite de fichier : format mentionné + verbe d'export.

    'Génère moi un pdf qui liste…' → 'pdf'. 'C'est quoi un pdf ?' → None.
    """
    lowered = (text or "").lower()
    fmt = detect_format(lowered)
    if fmt is None:
        return None
    if any(keyword in lowered for keyword in EXPORT_KEYWORDS):
        return fmt
    return None


def detect_format(text: str) -> str | None:
    lowered = (text or "").lower()
    for fmt in FORMATS:
        if fmt in lowered or f".{fmt}" in lowered:
            return fmt
    if any(word in lowered for word in ("présentation", "slides", "diapos", "powerpoint")):
        return "pptx"
    if any(word in lowered for word in ("tableur", "classeur", "feuille de calcul", "excel")):
        return "xlsx"
    if "word" in lowered:
        return "docx"
    return None


def create_export(message: Message, fmt: str, title: str, markdown_text: str) -> Export:
    if fmt not in _BUILDERS:
        raise ValidationError(f"Format inconnu : {fmt}.")
    if len(markdown_text or "") > settings.JEJEGPT_EXPORTS_MAX_INPUT_CHARS:
        raise ValidationError("Contenu source trop long pour un export.")
    payload = _BUILDERS[fmt](title, markdown_text)
    check_size(len(payload))
    check_quota(len(payload))
    name = safe_name(title or message.conversation.title, fmt)
    export = Export(message=message, format=fmt, file_name=name, size=len(payload))
    export.file.save(name, ContentFile(payload), save=False)
    export.save()
    return export
