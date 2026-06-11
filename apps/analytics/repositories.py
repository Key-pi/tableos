from apps.analytics.models import PartnerDailyMetric


class AnalyticsRepository:
    @staticmethod
    def range_for_partner(partner_id):
        return PartnerDailyMetric.objects.filter(partner_id=partner_id).order_by("-bucket_date")
