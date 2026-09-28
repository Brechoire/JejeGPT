"""Gabarits seedés Quiz + Fiche infographie (rejouables sans doublon)."""

from django.db import migrations


QUIZ_BODY = """Crée un quiz interactif en Markdown sur le sujet suivant :

**Sujet : {{sujet}}**

Génère **{{nombre}} questions** de compréhension, ni trop faciles ni piégeuses.

Pour chaque question :

### Question N — intitulé

- A) …
- B) …
- C) …
- D) …

<details>
<summary>Voir la réponse</summary>

**Bonne réponse : X** — explication courte (1 à 2 phrases).

</details>

Règles : une seule bonne réponse par question, les bonnes réponses réparties entre A/B/C/D, un niveau homogène, aucune question hors sujet. Termine par le score sous forme « Score : …/{{nombre}} » à compléter par le lecteur."""

FICHE_BODY = """Crée une fiche de synthèse lisible comme une infographie, en Markdown, sur le sujet suivant :

**Sujet : {{sujet}}**

## Chiffres clés

| Fait | Valeur | Date / source |
|---|---|---|
| … | … | … |

Ajoute au moins 5 lignes avec des valeurs vérifiables. N'invente aucun chiffre : si une donnée manque, écris « non disponible ».

## Dates repères

- **JJ/MM/AAAA** — événement.

## 5 points essentiels à retenir

1. …
2. …
3. …
4. …
5. …

Style : phrases courtes, **gras** pour l'essentiel, aucun paragraphe de plus de 3 phrases. Pensé pour être exporté en PDF."""


TEMPLATES = [
    (
        "Quiz interactif",
        "QCM auto-corrigé : sujet + nombre de questions, réponses repliées.",
        QUIZ_BODY,
        False,
    ),
    (
        "Fiche infographie",
        "Synthèse visuelle : tableau de chiffres, dates repères, 5 points clés.",
        FICHE_BODY,
        False,
    ),
]


def seed_forward(apps, schema_editor):
    TaskTemplate = apps.get_model("chat", "TaskTemplate")
    for name, description, body, suggest_web in TEMPLATES:
        TaskTemplate.objects.update_or_create(
            name=name,
            defaults={"description": description, "body": body, "suggest_web": suggest_web},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0018_trigger_fields'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
