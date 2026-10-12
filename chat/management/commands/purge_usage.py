from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from chat.models import UsageRecord

_CHUNK = 1000


class Command(BaseCommand):
    help = (
        "Supprime les lignes d'usage plus vieilles que JEJEGPT_USAGE_RETENTION_DAYS "
        "(dry-run par défaut). Les stats de l'onglet Utilisation portent sur 90 jours "
        "max : purger au-delà ne change pas l'affichage courant."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Applique réellement la suppression")

    def handle(self, *args, **options) -> None:
        limit = timezone.now() - timedelta(days=settings.JEJEGPT_USAGE_RETENTION_DAYS)
        old = UsageRecord.objects.filter(created_at__lt=limit)
        # 1 requête d'agrégat, jamais d'instanciation en masse.
        stats = old.aggregate(n=Count("id"), total=Sum("total_tokens"))
        if not stats["n"]:
            self.stdout.write("Aucune ligne d'usage à purger.")
            return
        self.stdout.write(
            f"{'SUPPRIMÉ' if options['apply'] else 'À SUPPRIMER'} : "
            f"{stats['n']} ligne(s), {stats['total'] or 0} tokens cumulés "
            f"(avant {limit.date().isoformat()})"
        )
        if options["apply"]:
            # Suppression par lots de PK : verrous courts, pas de DELETE géant.
            # Les lignes agrégées mensuellement avant purge restent à la charge
            # de l'opérateur si l'historique long doit être archivé.
            deleted = 0
            while True:
                batch = list(old.values_list("pk", flat=True)[:_CHUNK])
                if not batch:
                    break
                deleted += UsageRecord.objects.filter(pk__in=batch).delete()[0]
            self.stdout.write(self.style.SUCCESS(f"{deleted} ligne(s) d'usage supprimée(s)."))
        else:
            self.stdout.write(f"{stats['n']} ligne(s) — dry-run (ajoutez --apply).")
