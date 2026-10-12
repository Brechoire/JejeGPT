from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from chat.models import Export


class Command(BaseCommand):
    help = "Supprime les exports plus vieux que JEJEGPT_EXPORTS_RETENTION_DAYS (dry-run par défaut)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Applique réellement la suppression")

    def handle(self, *args, **options) -> None:
        limit = timezone.now() - timedelta(days=settings.JEJEGPT_EXPORTS_RETENTION_DAYS)
        old = Export.objects.filter(created_at__lt=limit)
        # 1 requête d'agrégat au lieu d'instancier tous les objets pour la taille.
        stats = old.aggregate(n=Count("id"), total=Sum("size"))
        if not stats["n"]:
            self.stdout.write("Aucun export à purger.")
            return
        # Aperçu borné (pas de chargement intégral) + parcours streaming à l'apply.
        preview = old.only("id", "file", "file_name", "size").order_by("id")[:50]
        for e in preview:
            self.stdout.write(f"{'SUPPRIMÉ' if options['apply'] else 'À SUPPRIMER'} : {e.file_name} ({e.size} o)")
        if (stats["n"] or 0) > 50:
            self.stdout.write(f"… et {(stats['n'] or 0) - 50} autre(s).")
        if options["apply"]:
            for e in old.only("id", "file").order_by("id").iterator(chunk_size=500):
                if e.file:
                    try:
                        e.file.delete(save=False)
                    except Exception:
                        pass
            count = old.delete()[0]
            self.stdout.write(self.style.SUCCESS(f"{count} export(s) supprimé(s)."))
        else:
            self.stdout.write(f"{stats['n']} export(s), {stats['total'] or 0} o — dry-run (ajoutez --apply).")
