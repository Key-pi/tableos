from django.db import models

from apps.partners.models import BotInstance, Partner


class PartnerRepository:
    @staticmethod
    def active() -> "models.QuerySet[Partner]":
        return Partner.objects.filter(status=Partner.Status.ACTIVE)


class BotInstanceRepository:
    @staticmethod
    def active() -> "models.QuerySet[BotInstance]":
        return BotInstance.objects.filter(is_active=True).select_related("partner")
