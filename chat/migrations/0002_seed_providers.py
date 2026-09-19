"""Préconfigure les providers OpenAI-compatibles (clés vides, saisie via l'UI).

Idempotent : rejouable sans écraser les clés déjà saisies (api_key jamais touchée).
"""
from django.db import migrations

SEEDS = [
    ("OpenCode Go", "https://opencode.ai/zen/go/v1"),
    ("OpenCode Zen", "https://opencode.ai/zen/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
]


def seed_providers(apps, schema_editor):
    Provider = apps.get_model("chat", "Provider")
    for name, base_url in SEEDS:
        Provider.objects.update_or_create(
            name=name, defaults={"base_url": base_url, "is_active": True}
        )


def unseed_providers(apps, schema_editor):
    Provider = apps.get_model("chat", "Provider")
    Provider.objects.filter(name__in=[name for name, _ in SEEDS]).delete()


class Migration(migrations.Migration):
    dependencies = [("chat", "0001_initial")]

    operations = [migrations.RunPython(seed_providers, unseed_providers)]
