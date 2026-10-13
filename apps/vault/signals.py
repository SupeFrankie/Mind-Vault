"""Remove files from disk when their Material row is deleted (including cascades)."""
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Material


@receiver(post_delete, sender=Material)
def delete_material_file(sender, instance, **kwargs):
    if instance.file:
        storage, name = instance.file.storage, instance.file.name
        # After commit: if the delete transaction rolls back, the file must survive.
        transaction.on_commit(lambda: storage.delete(name))
