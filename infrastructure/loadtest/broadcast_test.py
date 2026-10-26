"""Send a campaign to N guests of load partner 0 and time it (worker must run).

usage: python broadcast_test.py 2000
"""

import sys
import time

import django

django.setup()

from apps.notifications.models import BroadcastCampaign  # noqa: E402
from apps.notifications.services import send_broadcast_campaign  # noqa: E402
from apps.partners.models import Partner  # noqa: E402
from apps.users.models import GuestProfile, TelegramAccount  # noqa: E402
from apps.users.services import get_or_create_guest_profile  # noqa: E402

N = int(sys.argv[1])
partner = Partner.objects.get(slug="lt-0000")
have = GuestProfile.objects.filter(partner=partner).count()
for k in range(have, N):
    get_or_create_guest_profile(partner_id=partner.id, telegram_id=900000000 + k)
TelegramAccount.objects.filter(guest_profiles__partner=partner).update(is_blocked=False)
audience = GuestProfile.objects.filter(partner=partner, is_subscribed=True).count()
campaign = BroadcastCampaign.objects.create(
    partner=partner, name=f"load {time.time():.0f}", message="Знижка 10% сьогодні ввечері!"
)
start = time.monotonic()
send_broadcast_campaign(campaign)
while True:
    time.sleep(2)
    campaign.refresh_from_db()
    done = campaign.delivered_count + campaign.failed_count + campaign.unknown_count
    print(
        f"{time.monotonic() - start:6.1f}s status={campaign.status} "
        f"delivered={campaign.delivered_count} failed={campaign.failed_count}",
        flush=True,
    )
    if campaign.status in ("sent", "failed"):
        break
elapsed = time.monotonic() - start
print(f"audience={audience} delivered={campaign.delivered_count} in {elapsed:.1f}s "
      f"= {campaign.delivered_count / elapsed:.1f} msg/s")
