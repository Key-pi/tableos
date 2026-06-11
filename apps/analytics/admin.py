from django.contrib import admin

from apps.analytics.models import PartnerDailyMetric
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(PartnerDailyMetric)
class PartnerDailyMetricAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ANALYTICS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "partner",
        "bucket_date",
        "revenue",
        "orders_count",
        "average_check",
        "active_guests",
    )
    list_filter = ("partner", "bucket_date")
    search_fields = ("partner__name",)
