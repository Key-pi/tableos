"""Admin language (PQ-040): each user works in their own language.

The user's choice (``User.language``) or, without one, the platform language
(``LANGUAGE_CODE``, Ukrainian by default). Reads the attribute only, so
``core`` does not import the users app.
"""

from django.urls import reverse
from django.utils import translation

from core.i18n import default_language, normalize_language
from core.labels import one_venue


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        chosen = getattr(user, "language", "") if getattr(user, "is_authenticated", False) else ""
        language = normalize_language(chosen) or default_language()
        with translation.override(language):
            request.LANGUAGE_CODE = language
            response = self.get_response(request)
            response.headers.setdefault("Content-Language", language)
        return response


class VenueLabelsMiddleware:
    """Inside one venue's Admin records name themselves without the venue
    (``core/labels.py``, admin step 4): the owner and staff see one venue.
    The platform, the bot and tasks keep full labels."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefix = None

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False) or user.is_superuser:
            return self.get_response(request)
        if self.admin_prefix is None:
            self.admin_prefix = reverse("admin:index")
        if not request.path.startswith(self.admin_prefix):
            return self.get_response(request)
        # Pages are rendered inside (Django renders a TemplateResponse before
        # it returns through the middleware).
        with one_venue():
            return self.get_response(request)
