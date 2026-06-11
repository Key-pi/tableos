from django.db import models


class BonusProgramType(models.TextChoices):
    CASHBACK = "cashback", "Cashback"
    VISIT = "visit", "Visit"
    MILESTONE = "milestone", "Milestone"


class BonusTransactionType(models.TextChoices):
    ACCRUAL = "accrual", "Accrual"
    REDEMPTION = "redemption", "Redemption"
    EXPIRATION = "expiration", "Expiration"
    MANUAL = "manual", "Manual"
