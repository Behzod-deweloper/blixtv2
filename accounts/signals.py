from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import User


@receiver(pre_delete, sender=User, dispatch_uid="accounts.delete_user_avatar")
def delete_user_avatar(sender, instance, **kwargs):
    if instance.avatar and instance.avatar.name:
        instance.avatar.delete(save=False)
