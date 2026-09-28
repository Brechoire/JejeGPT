"""Badges de capacités dans le sélecteur de modèle (zéro requête : ligne cache déjà chargée)."""
from django import template

from chat.providers import model_controls

register = template.Library()


@register.filter
def model_badges(model, provider=None):
    """Contrôles affichables d'un LLMModel : route/effort/web/thinking/known."""
    base_url = str(getattr(provider, "base_url", "") or "")
    return model_controls(model, base_url)
