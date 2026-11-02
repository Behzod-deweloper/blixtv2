import os
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


def user_avatar_upload_to(instance, filename):
    extension = os.path.splitext(filename)[1].lower().lstrip(".")
    return f"users/{instance.pk}/avatar/avatar-{uuid.uuid4().hex}.{extension}"


class User(AbstractUser):
    email = models.EmailField(unique=True)
    avatar = models.FileField(upload_to=user_avatar_upload_to, blank=True, max_length=500)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
        ]