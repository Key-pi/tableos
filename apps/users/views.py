"""The Admin user's own language (PQ-040), chosen in the Admin header; into
Admin by a one-time link from the bot (PQ-045)."""

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login as auth_login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core.admin_mixins import ADMIN_POINT_SESSION_KEY
from core.i18n import LANGUAGES, _


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


@require_http_methods(["GET", "POST"])
def admin_enter(request: HttpRequest, token: str) -> HttpResponse:
    """Into Admin by a one-time link from the bot (PQ-045, step 2). GET only
    shows whose link it is and «Войти»: a link preview does not spend it;
    POST spends it and logs in at the link's venue."""

    from django.contrib import admin

    from apps.users.admin_login import AdminLoginError, redeem_admin_login_link, usable_link

    context = {**admin.site.each_context(request), "title": _("Вход в Admin")}
    if request.method == "POST":
        try:
            link = redeem_admin_login_link(token)
        except AdminLoginError as exc:
            context["error"] = str(exc)
            return render(request, "admin/enter_link.html", context, status=410)
        auth_login(request, link.user, backend="django.contrib.auth.backends.ModelBackend")
        # A network's owner or manager opens the venue whose bot gave the link.
        request.session[ADMIN_POINT_SESSION_KEY] = str(link.partner_id)
        return HttpResponseRedirect(reverse("admin:index"))
    link = usable_link(token)
    if link is None:
        context["error"] = _(
            "Ссылка уже использована или устарела. Нажмите «🔐 Войти в Admin» в боте ещё раз."
        )
        return render(request, "admin/enter_link.html", context, status=410)
    context.update(venue=link.partner.name, person=link.user.get_full_name() or link.user.username)
    return render(request, "admin/enter_link.html", context)
