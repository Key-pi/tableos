import uuid

from django.db import models

from core.i18n import gettext_lazy as _lazy


class UUIDPrimaryKeyModel(models.Model):
    """Abstract base model with UUID primary key for all domain entities."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDPrimaryKeyModel):
    """Abstract base model that tracks creation and update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_lazy("Создано"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_lazy("Изменено"))

    class Meta:
        abstract = True


class PartnerBoundModel(TimeStampedModel):
    """Abstract base model for records that belong to a specific partner."""

    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)ss",
        verbose_name=_lazy("Заведение"),
    )

    class Meta:
        abstract = True
