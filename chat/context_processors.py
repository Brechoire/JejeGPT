"""Injecte la taille de texte globale dans tous les templates."""
from __future__ import annotations

from typing import Any


def text_scale(request) -> dict[str, Any]:
    from .models import AppSettings

    try:
        return {"text_scale": AppSettings.get_cached().scale_factor()}
    except Exception:
        return {"text_scale": 100}
