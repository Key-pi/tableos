"""Admin login throttle (AA-038): too many failed passwords lock the account.

Counted per account name (case-insensitive) in the Django cache — Redis in
production, so every web worker sees the same count. After MAX_FAILURES
failed attempts within WINDOW_SECONDS the login form answers 429 until the
window passes; a successful login clears the count. Per account rather than
per IP: behind a reverse proxy every request comes from the proxy's address.
"""

from __future__ import annotations

import hashlib
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCKED_MESSAGE = (
    "Слишком много неудачных попыток входа. Попробуйте через 15 минут "
    "или попросите администратора проверить учётную запись."
)


def _key(username: str) -> str:
    digest = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()
    return f"admin-login-failures:{digest}"


def throttle_login(login_view):
    @wraps(login_view)
    def view(request, *args, **kwargs):
        if request.method != "POST":
            return login_view(request, *args, **kwargs)
        key = _key(request.POST.get("username", ""))
        if (cache.get(key) or 0) >= MAX_FAILURES:
            return HttpResponse(
                LOCKED_MESSAGE, status=429, content_type="text/plain; charset=utf-8"
            )
        response = login_view(request, *args, **kwargs)
        if request.user.is_authenticated:
            cache.delete(key)
        else:
            if not cache.add(key, 1, WINDOW_SECONDS):
                try:
                    cache.incr(key)
                except ValueError:
                    cache.set(key, 1, WINDOW_SECONDS)
        return response

    return view
