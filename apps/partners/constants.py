from django.db import models


class PartnerStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class BotConnectionMode(models.TextChoices):
    POLLING = "polling", "Polling"
    WEBHOOK = "webhook", "Webhook"
