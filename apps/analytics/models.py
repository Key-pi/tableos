from django.db import models

from core.database.models import PartnerBoundModel


class PartnerDailyMetric(PartnerBoundModel):
    """Daily aggregated KPI snapshot for partner dashboards and reporting."""

    bucket_date = models.DateField()
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_count = models.PositiveIntegerField(default=0)
    average_check = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    active_guests = models.PositiveIntegerField(default=0)
    repeat_visits = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-bucket_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "bucket_date"],
                name="unique_partner_metric_per_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.bucket_date}"
