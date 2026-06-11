from apps.menu.models import MenuItem


class MenuItemRepository:
    @staticmethod
    def active_for_partner(partner_id):
        return MenuItem.objects.filter(
            partner_id=partner_id,
            is_available=True,
        ).select_related("category")
