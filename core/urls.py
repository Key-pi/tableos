from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.orders.models import Order
from apps.partners.models import BotInstance, Partner
from apps.tables.models import Table


def healthcheck(_: HttpRequest) -> HttpResponse:
    return HttpResponse("ok")


def demo_home(_: HttpRequest) -> HttpResponse:
    partners_count = Partner.objects.count()
    bots_count = BotInstance.objects.filter(is_active=True).count()
    tables_count = Table.objects.filter(is_active=True).count()
    orders_count = Order.objects.count()
    # Keep the landing page template-free so the backend is demoable even
    # before we introduce a separate frontend or Telegram WebApp shell.
    html = f"""
    <html>
      <head>
        <title>TableOS Demo</title>
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
          <h1>TableOS backend is running</h1>
          <p>White-label Telegram platform demo environment for venue partners.</p>
          <div class="stats">
            <div class="stat"><strong>Partners</strong><br>{partners_count}</div>
            <div class="stat"><strong>Active bots</strong><br>{bots_count}</div>
            <div class="stat"><strong>Tables</strong><br>{tables_count}</div>
            <div class="stat"><strong>Orders</strong><br>{orders_count}</div>
          </div>
          <p><a href="/admin/">Open Django admin</a> · <a href="/health/">Healthcheck</a></p>
          <p>Recommended local bootstrap:</p>
          <p>
            <code>
              python manage.py seed_demo --with-admin --with-manager
              --bot-username your_bot_username
            </code>
          </p>
        </div>
      </body>
    </html>
    """
    return HttpResponse(html)


urlpatterns = [
    path("", demo_home, name="demo-home"),
    path("admin/", admin.site.urls),
    path("health/", healthcheck, name="healthcheck"),
]
