"""Backfill web=True pour les modèles Responses (outil natif vérifié le 17/09/2026).

Ne met que True (jamais de False) pour ne pas écraser un décochage manuel :
un modèle explicitement décoché puis re-taggué True est un cas assumé rare.
"""
from django.db import migrations


def backfill_web(apps, schema_editor):
    from chat.providers import MODEL_CAPABILITIES

    LLMModel = apps.get_model("chat", "LLMModel")
    ids = [mid for mid, caps in MODEL_CAPABILITIES.items() if caps.get("web")]
    LLMModel.objects.filter(model_id__in=ids).update(supports_web=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("chat", "0006_usagerecord")]

    operations = [migrations.RunPython(backfill_web, noop)]
