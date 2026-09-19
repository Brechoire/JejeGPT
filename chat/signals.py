from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Export


def _delete_storage_file(field_file) -> None:
    storage, name = field_file.storage, field_file.name
    if not name:
        return

    def _cleanup() -> None:
        try:
            if storage.exists(name):
                storage.delete(name)
        except Exception:
            pass

    transaction.on_commit(_cleanup)


@receiver(post_delete, sender=Export)
def delete_export_file(sender, instance: Export, **kwargs) -> None:
    if instance.file:
        _delete_storage_file(instance.file)
