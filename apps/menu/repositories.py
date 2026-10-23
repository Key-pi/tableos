from apps.menu.models import MenuItem


class MenuItemRepository:
    @staticmethod
    def active_for_partner(partner_id, *, channel: str | None = None):
        queryset = MenuItem.objects.filter(
            partner_id=partner_id,
            is_available=True,
            category__is_active=True,
        ).select_related("category")
        if channel is not None:
            queryset = queryset.filter(**{MenuItem.CHANNEL_FIELDS[channel]: True})
        return queryset
