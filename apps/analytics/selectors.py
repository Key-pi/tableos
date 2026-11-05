"""Reads of the analytics module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric
from apps.partners.models import Partner


@dataclass(frozen=True)
class PointSummary:
    partner: Partner | None  # None: the whole network
    revenue: Decimal
    orders: int
    sales: int
    # Bonuses this venue accrued and redeemed (PQ-047): with a shared balance
    # one venue's bonuses may be spent at another.
    bonus_accrued: Decimal = Decimal("0.00")
    bonus_redeemed: Decimal = Decimal("0.00")

    @property
    def average_check(self) -> Decimal:
        # Per sale (paid bill or quick sale), as in the daily metrics.
        if not self.sales:
            return Decimal("0.00")
        return (self.revenue / self.sales).quantize(Decimal("0.01"))


def _sales(metric: PartnerDailyMetric) -> int:
    if metric.sales_count or not metric.average_check:
        return metric.sales_count
    # Days counted before sales were stored: revenue / average check.
    return int((metric.revenue / metric.average_check).to_integral_value())


def network_summary(partner_ids, *, days: int) -> list[PointSummary]:
    """Revenue and orders of each venue for the last ``days`` days, from the
    daily metrics (refreshed every 15 minutes), plus the network total last
    (PQ-047). The caller passes only venues the reader may open."""

    since = timezone.localdate() - timedelta(days=days - 1)
    totals: dict = {}
    for metric in PartnerDailyMetric.objects.filter(
        partner_id__in=partner_ids, bucket_date__gte=since
    ):
        revenue, orders, sales = totals.get(metric.partner_id, (Decimal("0.00"), 0, 0))
        totals[metric.partner_id] = (
            revenue + metric.revenue,
            orders + metric.orders_count,
            sales + _sales(metric),
        )
    bonuses = _bonuses_by_partner(partner_ids, since=since)
    rows = []
    for partner in Partner.objects.filter(id__in=partner_ids).order_by("name"):
        revenue, orders, sales = totals.get(partner.id, (Decimal("0.00"), 0, 0))
        accrued, redeemed = bonuses.get(partner.id, (Decimal("0.00"), Decimal("0.00")))
        rows.append(
            PointSummary(
                partner=partner, revenue=revenue, orders=orders, sales=sales,
                bonus_accrued=accrued, bonus_redeemed=redeemed,
            )
        )
    rows.append(
        PointSummary(
            partner=None,
            revenue=sum((row.revenue for row in rows), Decimal("0.00")),
            orders=sum(row.orders for row in rows),
            sales=sum(row.sales for row in rows),
            bonus_accrued=sum((row.bonus_accrued for row in rows), Decimal("0.00")),
            bonus_redeemed=sum((row.bonus_redeemed for row in rows), Decimal("0.00")),
        )
    )
    return rows


def _bonuses_by_partner(partner_ids, *, since) -> dict:
    """{venue id: (accrued, redeemed)} from the bonus ledger since a day."""

    from apps.bonuses.models import BonusTransaction

    start = timezone.make_aware(datetime.combine(since, time.min))
    kinds = BonusTransaction.TransactionType
    rows = (
        BonusTransaction.objects.filter(partner_id__in=partner_ids, created_at__gte=start)
        .values("partner_id")
        .annotate(
            accrued=Sum(
                "amount", filter=Q(transaction_type__in=[kinds.ACCRUAL, kinds.MANUAL]), default=0
            ),
            redeemed=Sum("amount", filter=Q(transaction_type=kinds.REDEMPTION), default=0),
        )
    )
    return {
        row["partner_id"]: (
            Decimal(row["accrued"]).quantize(Decimal("0.01")),
            abs(Decimal(row["redeemed"])).quantize(Decimal("0.01")),
        )
        for row in rows
    }
