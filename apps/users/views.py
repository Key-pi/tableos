"""The Admin user's own language (PQ-040), chosen in the Admin header."""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.i18n import LANGUAGES


@require_POST
@staff_member_required
def set_admin_language(request: HttpRequest) -> HttpResponseRedirect:
    language = request.POST.get("language", "")
    if language in LANGUAGES and request.user.language != language:
        request.user.language = language
        request.user.save(update_fields=["language"])
    target = request.POST.get("next") or "/admin/"
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = "/admin/"
    return HttpResponseRedirect(target)
