from apps.integrations.poster.selectors import only_linked, own_menu_hidden
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
            # «Только меню Poster» (step 7): as ``apps/menu/catalog.py:not_sold_in``.
            if own_menu_hidden(partner_id, channel):
                queryset = only_linked(queryset)
        return queryset
