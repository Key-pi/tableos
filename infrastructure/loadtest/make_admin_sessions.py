"""Log in the owners of the first K load partners; write admin_sessions.json."""

import json
import pathlib
import sys

import django

django.setup()

from importlib import import_module  # noqa: E402

from django.conf import settings  # noqa: E402
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY  # noqa: E402
from django.contrib.sessions.backends.cache import SessionStore as CacheStore  # noqa: E402,F401

from apps.orders.models import Order  # noqa: E402
from apps.users.constants import AdminAccessPreset  # noqa: E402
from apps.users.models import AdminAccessProfile, User  # noqa: E402

K = int(sys.argv[1])
store_cls = import_module(settings.SESSION_ENGINE).SessionStore
out = []
for i in range(K):
    user = User.objects.get(username=f"lt{i}_owner")
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    AdminAccessProfile.objects.update_or_create(
        user=user,
        defaults={"partner_id": user.partner_id, "preset": AdminAccessPreset.OWNER,
                  "is_partner_owner": True, "can_access_admin": True, "is_active": True},
    )
    session = store_cls()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    ids = list(Order.objects.filter(partner_id=user.partner_id).values_list("id", flat=True)[:5])
    out.append({"sessionid": session.session_key,
                "detail_paths": [f"/admin/orders/order/{pk}/change/" for pk in ids]})
pathlib.Path(__file__).with_name("admin_sessions.json").write_text(json.dumps(out))
print(len(out), "sessions")
