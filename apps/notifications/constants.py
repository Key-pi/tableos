from django.db import models


class BroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    SENT = "sent", "Sent"
