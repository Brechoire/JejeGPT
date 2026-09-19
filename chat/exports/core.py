"""Noms de fichiers et limites pour les exports JejeGPT."""
from __future__ import annotations

import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum

from ..models import Export

_RE_SPACES = re.compile(r"\s+")
_RE_BAD = re.compile(r"[\W_]+", re.UNICODE)


def safe_name(title: str, fmt: str) -> str:
    """Nom assaini, accents FR conservés, sans caractères dangereux ni path traversal."""
    base = _RE_SPACES.sub(" ", (title or "").strip()) or "export"
    base = unicodedata.normalize("NFKC", base).replace("/", "-").replace("\\", "-")
    base = _RE_BAD.sub("-", base).strip("-. ")
    base = _RE_SPACES.sub("-", base)[:120].strip("-.")
    return f"{base or 'export'}.{fmt}"


def check_size(size: int) -> None:
    if size <= 0:
        raise ValidationError("Export vide.")
    if size > settings.JEJEGPT_EXPORTS_MAX_BYTES:
        raise ValidationError("Export trop volumineux.")


def check_quota(additional: int) -> None:
    total = Export.objects.aggregate(total=Sum("size"))["total"] or 0
    if total + additional > settings.JEJEGPT_EXPORTS_QUOTA_BYTES:
        raise ValidationError("Quota exports dépassé. Lancez la purge ou augmentez JEJEGPT_EXPORTS_QUOTA_BYTES.")
