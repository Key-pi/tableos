from django.db import models


class MenuItemType(models.TextChoices):
    INTERNAL = "internal", "Internal"
    PARTNER = "partner", "Partner"
