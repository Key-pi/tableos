from apps.menu.models import MenuCategory


def category_tree_for_partner(partner_id):
    return MenuCategory.objects.filter(
        partner_id=partner_id,
        is_active=True,
    ).prefetch_related("items")
