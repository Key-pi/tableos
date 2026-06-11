from apps.partners.models import Partner


def get_partner_by_slug(slug: str) -> Partner:
    return Partner.objects.get(slug=slug)
