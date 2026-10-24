"""Admin language (PQ-040): each user works in their own language.

The user's choice (``User.language``) or, without one, the platform language
(``LANGUAGE_CODE``, Ukrainian by default). Reads the attribute only, so
``core`` does not import the users app.
"""

from django.utils import translation

from core.i18n import default_language, normalize_language


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
