"""The public address of this site, for links that leave it (PQ-045).

``WEBAPP_BASE_URL`` (the HTTPS address of the Mini App and Admin in
production, PQ-048); without it — the first trusted origin
(``DJANGO_CSRF_TRUSTED_ORIGINS``, locally ``http://localhost:8000``).
"""

from django.conf import settings


def site_address() -> str:
    if settings.WEBAPP_BASE_URL:
        return settings.WEBAPP_BASE_URL.rstrip("/")
    origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", None) or []
    return origins[0].rstrip("/") if origins else "http://localhost:8000"
