"""Poster's return after the owner's login (PQ-058, step 2).

Poster sends the browser back here with ``code`` and ``account``. Only a
press of «Подключить Poster» in this browser's Admin session leads anywhere
(``services.take_started_connection``); everything else goes back to the
Poster page of Admin with the reason.
"""

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.integrations.poster import services
from core.i18n import _


@require_GET
def oauth_callback(request: HttpRequest) -> HttpResponse:
    if not (request.user.is_authenticated and request.user.is_staff):
        return redirect_to_login(request.get_full_path(), login_url=reverse("admin:login"))
    poster_page = reverse("admin:poster_posterconnection_changelist")
    if request.GET.get("error"):
        request.session.pop(services.SESSION_KEY, None)
        messages.warning(request, _("Вход в Poster отменён — Poster не подключён."))
        return HttpResponseRedirect(poster_page)
    try:
        partner_id = services.take_started_connection(session=request.session, user=request.user)
        connection = services.finish_connection(
            user=request.user,
            partner_id=partner_id,
            account=request.GET.get("account", ""),
            code=request.GET.get("code", ""),
        )
    except services.PosterConnectError as exc:
        messages.warning(request, str(exc))
        return HttpResponseRedirect(poster_page)
    if connection.status == connection.Status.ACTIVE:
        messages.success(request, _("Poster подключён."))
    else:
        messages.success(request, _("Poster подключён. Выберите, какая точка — это заведение."))
    return HttpResponseRedirect(
        reverse("admin:poster_posterconnection_change", args=[connection.pk]),
    )
