from apps.analytics.models import PartnerDailyMetric


def latest_partner_metrics(partner_id):
    return PartnerDailyMetric.objects.filter(partner_id=partner_id).order_by("-bucket_date").first()
