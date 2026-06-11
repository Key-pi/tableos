from django.db import models


class TableSessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"
    TIMED_OUT = "timed_out", "Timed out"
