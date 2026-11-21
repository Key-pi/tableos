"""Poster's return after the owner's login (PQ-058, step 2) and Poster's
webhooks (step 4, ``webhook``).

Poster sends the browser back here with ``code`` and ``account``. Only a
press of «Подключить Poster» in this browser's Admin session leads anywhere
(``services.take_started_connection``); everything else goes back to the
Poster page of Admin with the reason. A spot that starts working at once
brings the menu at once (``bring_menu``, step 3).
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.integrations.poster import services, webhooks
from apps.integrations.poster.menu_sync import PosterSyncError, sync_menu
from core.i18n import _


def bring_menu(request: HttpRequest, connection) -> None:
    """The menu from Poster now, with what happened in messages; the
    connection stays whatever Poster answers."""

    try:
        report = sync_menu(connection_id=connection.pk, user=request.user)
    except PosterSyncError as exc:
        messages.warning(request, _(
            "Меню из Poster не загрузилось: {reason}. Нажмите «Обновить меню» позже.",
            reason=str(exc).rstrip("."),
        ))
        return
    messages.success(request, report.summary())
    for note in report.notes():
        messages.warning(request, note)


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
        bring_menu(request, connection)
    else:
        messages.success(request, _("Poster подключён. Выберите, какая точка — это заведение."))
    return HttpResponseRedirect(
        reverse("admin:poster_posterconnection_change", args=[connection.pk]),
    )


@csrf_exempt
@require_POST
def webhook(request: HttpRequest) -> HttpResponse:
    """Poster's event: kept if signed, done by a task (``webhooks``)."""

    body = webhooks.read_body(request.body)
    if body is None or not webhooks.signature_ok(body, settings.POSTER_APPLICATION_SECRET):
        return JsonResponse({"status": "error"}, status=403)
    webhooks.receive(body)
    return JsonResponse({"status": "accept"})
