"""Gabarit seedé « Recherche approfondie » (rejouable sans doublon)."""

from django.db import migrations


NAME = "Recherche approfondie"

BODY = """Effectue une recherche approfondie et récente sur le sujet suivant :

**Sujet : {{sujet}}**

Je souhaite obtenir les **{{nombre}} actualités les plus importantes, pertinentes et récentes** concernant ce sujet.

## 1. Recherche des informations

Recherche les informations sur plusieurs sources fiables et reconnues, françaises et internationales.

Privilégie notamment :

* les sources officielles ;
* les sites spécialisés dans le domaine concerné ;
* les médias reconnus ;
* les publications d'entreprises ou d'organisations directement concernées ;
* les publications scientifiques ou techniques lorsque cela est pertinent ;
* les annonces officielles, documentations, blogs techniques ou dépôts GitHub lorsqu'il s'agit d'un sujet technologique.

Ne te limite pas aux sources françaises.

Tu peux utiliser des sources :

* américaines ;
* britanniques ;
* chinoises ;
* japonaises ;
* coréennes ;
* allemandes ;
* espagnoles ;
* ou provenant de n'importe quel autre pays.

Lorsque la source n'est pas en français, **traduis et reformule les informations en français**.

Ne traduis pas mécaniquement le texte original : rédige un véritable article en français en conservant fidèlement le sens de l'information.

## 2. Vérification des informations

Avant de présenter une actualité :

* vérifie sa date de publication ;
* vérifie si l'événement annoncé s'est réellement produit ou s'il s'agit seulement d'une annonce ;
* croise l'information avec plusieurs sources lorsque cela est possible ;
* privilégie la source originale lorsqu'elle existe ;
* évite les rumeurs non confirmées ;
* indique clairement lorsqu'une information reste incertaine ou non confirmée ;
* évite de présenter plusieurs articles parlant exactement du même événement.

Si plusieurs médias reprennent la même information, regroupe-les dans une seule actualité et utilise les différentes publications comme sources complémentaires.

## 3. Classement

Sélectionne les **{{nombre}} actualités les plus intéressantes**.

Classe-les en priorité selon :

1. leur importance ;
2. leur nouveauté ;
3. leur impact potentiel ;
4. leur intérêt pour une personne qui suit régulièrement le sujet {{sujet}}.

Ne remplis pas artificiellement la sélection avec des informations mineures s'il n'existe pas suffisamment d'actualités réellement pertinentes.

## 4. Présentation de chaque actualité

Pour chaque actualité, utilise une présentation claire et visuellement agréable.

### [Numéro] — Titre de l'actualité

**Date :** JJ/MM/AAAA

**Résumé**

Rédige un résumé clair de **minimum 5 phrases**.

Le résumé doit expliquer :

* ce qui s'est passé ;
* qui est concerné ;
* le contexte ;
* les principales informations à retenir ;
* les éventuelles conséquences.

Le texte doit être compréhensible même pour quelqu'un qui n'a pas lu les sources originales.

**Pourquoi cette information est importante**

Explique concrètement pourquoi cette actualité mérite d'être suivie.

Indique notamment :

* son impact potentiel ;
* les personnes, entreprises ou secteurs concernés ;
* ce que cela pourrait changer ;
* les conséquences à court ou moyen terme.

**Exemple / aspect technique**

Lorsque l'information possède un aspect technique, ajoute une explication ou un exemple concret.

Cela peut être :

* un exemple de code ;
* une commande ;
* une configuration ;
* une comparaison avant/après ;
* un scénario d'utilisation ;
* une explication simplifiée d'une technologie ;
* un exemple concret permettant de comprendre l'impact de l'annonce.

Ne crée cette partie que lorsqu'elle apporte réellement quelque chose.

**Sources**

Indique les sources utilisées avec :

* le nom du média, organisme ou entreprise ;
* le titre de la publication si disponible ;
* la date de publication ;
* le lien direct vers la source.

Privilégie les liens vers l'article original plutôt que vers une page d'accueil.

---

## 5. Résumé global

Après les {{nombre}} actualités, crée une section :

# Résumé global

Rédige une synthèse permettant de comprendre rapidement ce qui s'est passé autour de **{{sujet}}** pendant la période étudiée.

La synthèse doit faire ressortir :

* les grandes tendances ;
* les annonces les plus importantes ;
* les changements observés ;
* les sujets qui reviennent régulièrement ;
* les éventuelles évolutions technologiques, économiques ou stratégiques ;
* les éléments qui pourraient devenir importants dans les semaines ou mois suivants.

Ajoute ensuite une section :

### Les 5 informations essentielles à retenir

Présente les cinq informations majeures sous forme de points courts et facilement lisibles.

Puis :

### Tendances à surveiller

Identifie les sujets qui méritent d'être surveillés prochainement.

Ne fais pas de prédictions présentées comme certaines. Distingue clairement :

* les faits ;
* les annonces ;
* les hypothèses ;
* les tendances observées.

## 6. Style de rédaction

La présentation doit être :

* professionnelle ;
* claire ;
* moderne ;
* agréable à lire ;
* bien hiérarchisée avec des titres et sous-titres ;
* suffisamment détaillée sans devenir inutilement longue.

Utilise le Markdown pour améliorer la lisibilité.

Utilise le **gras** pour les informations importantes.

Évite :

* le remplissage ;
* les répétitions ;
* les titres sensationnalistes ;
* les formulations vagues ;
* le copier-coller des articles sources.

Je veux une **véritable synthèse journalistique et documentaire**, rédigée à partir de plusieurs sources, et non une simple compilation de résultats de recherche.

## 7. Règle importante

N'invente aucune information.

Lorsqu'une donnée n'est pas vérifiable, indique-le explicitement.

Lorsqu'une source contredit une autre source fiable, explique brièvement le désaccord.

Toutes les informations importantes doivent pouvoir être reliées à une source identifiable."""

DESCRIPTION = "Veille documentaire structurée : sujet + nombre d'actualités, sources vérifiées et synthèse."


def seed_forward(apps, schema_editor):
    TaskTemplate = apps.get_model("chat", "TaskTemplate")
    TaskTemplate.objects.update_or_create(
        name=NAME,
        defaults={"description": DESCRIPTION, "body": BODY, "suggest_web": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0012_tasktemplate'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
