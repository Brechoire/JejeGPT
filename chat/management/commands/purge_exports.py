from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import Export


class Command(BaseCommand):
    help = "Supprime les exports plus vieux que JEJEGPT_EXPORTS_RETENTION_DAYS (dry-run par défaut)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Applique réellement la suppression")

    def handle(self, *args, **options) -> None:
        limit = timezone.now() - timedelta(days=settings.JEJEGPT_EXPORTS_RETENTION_DAYS)
        old = Export.objects.filter(created_at__lt=limit)
        total_size = sum(e.size for e in old)
        if not old.exists():
            self.stdout.write("Aucun export à purger.")
            return
        for e in old:
            self.stdout.write(f"{'SUPPRIMÉ' if options['apply'] else 'À SUPPRIMER'} : {e.file_name} ({e.size} o)")
        if options["apply"]:
            for e in old:
                if e.file:
                    try:
                        e.file.delete(save=False)
                    except Exception:
                        pass
            count = old.delete()[0]
            self.stdout.write(self.style.SUCCESS(f"{count} export(s) supprimé(s)."))
        else:
            self.stdout.write(f"{old.count()} export(s), {total_size} o — dry-run (ajoutez --apply).")
