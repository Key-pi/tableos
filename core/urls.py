from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.orders.models import Order
from apps.partners.models import BotInstance, Partner
from apps.tables.models import Table
from apps.users.views import set_admin_language
from core.i18n import _
from core.security.login_throttle import throttle_login


def healthcheck(_: HttpRequest) -> HttpResponse:
    return HttpResponse("ok")


def demo_home(request: HttpRequest) -> HttpResponse:
    if not settings.DEBUG:
        # The demo page is public: in production it must not disclose
        # platform-wide counts of partners, bots, tables or orders.
        return HttpResponse("ok")
    partners_count = Partner.objects.count()
    bots_count = BotInstance.objects.filter(is_active=True).count()
    tables_count = Table.objects.filter(is_active=True).count()
    orders_count = Order.objects.count()
    # Keep the landing page template-free so the backend is demoable even
    # before we introduce a separate frontend or Telegram WebApp shell.
    texts = {
        "title": _("Botera Shop — демо"),
        "heading": _("Botera Shop работает"),
        "intro": _(
            "Демо-окружение: Telegram-боты заказов и лояльности для заведений и магазинов."
        ),
        "partners": _("Заведений"),
        "bots": _("Активных ботов"),
        "tables": _("Столов"),
        "orders": _("Заказов"),
        "admin": _("Открыть Admin"),
        "health": _("Проверка работы"),
        "demo": _("Демо-данные (логины, ссылки на столы):"),
    }
    html = f"""
    <html lang="{request.LANGUAGE_CODE}">
      <head>
        <meta charset="utf-8">
        <title>{texts["title"]}</title>
        <style>
          body {{
            font-family: Georgia, serif;
            margin: 40px;
            color: #1f2937;
            background: #f8f5ef;
          }}
          .card {{
            max-width: 760px;
            padding: 24px 28px;
            background: #fffdf8;
            border: 1px solid #e5dccd;
            border-radius: 18px;
          }}
          h1 {{ margin-top: 0; }}
          .stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 24px 0; }}
          .stat {{
            padding: 12px 16px;
            border-radius: 12px;
            background: #f4ecde;
            min-width: 120px;
          }}
          code {{ background: #f1ede5; padding: 2px 6px; border-radius: 6px; }}
          a {{ color: #8a4b08; }}
        </style>
      </head>
      <body>
        <div class="card">
          <h1>{texts["heading"]}</h1>
          <p>{texts["intro"]}</p>
          <div class="stats">
            <div class="stat"><strong>{texts["partners"]}</strong><br>{partners_count}</div>
            <div class="stat"><strong>{texts["bots"]}</strong><br>{bots_count}</div>
            <div class="stat"><strong>{texts["tables"]}</strong><br>{tables_count}</div>
            <div class="stat"><strong>{texts["orders"]}</strong><br>{orders_count}</div>
          </div>
          <p><a href="/admin/">{texts["admin"]}</a> · <a href="/health/">{texts["health"]}</a></p>
          <p>{texts["demo"]}
            <code>docker compose run --rm web python manage.py seed_baseline_demo
            --keep-existing</code></p>
        </div>
      </body>
    </html>
    """
    return HttpResponse(html)


urlpatterns = [
    path("", demo_home, name="demo-home"),
    # AA-038: failed Admin logins are counted per account (login_throttle).
    path("admin/login/", throttle_login(admin.site.login), name="admin-login-throttled"),
    # PQ-040: the language switch in the Admin header saves the user's language.
    path("admin/language/", set_admin_language, name="admin-language"),
    path("admin/", admin.site.urls),
    path("health/", healthcheck, name="healthcheck"),
]
