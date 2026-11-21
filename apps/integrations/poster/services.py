"""Connecting a venue to Poster (PQ-058, step 2; plan — docs/architecture/POSTER.md).

1. The venue's owner presses «Подключить Poster» in Admin (a POST):
   ``start_connection`` remembers the venue in the Django session and gives
   the address of Poster's login. Poster does not return ``state`` (step 0),
   so the session is what ties Poster's return to this press; it is spent
   once and lives 15 minutes.
2. Poster returns to ``views.oauth_callback``: ``take_started_connection``
   checks the session, then ``finish_connection`` trades the code for a
   token and reads the account's spots — both requests before any
   transaction (AGENTS.md) — and saves the connection under a lock of the
   venue's row. One spot starts working at once; several wait for the
   owner's choice (``choose_spot``).
3. ``disconnect`` forgets the token; the record and its history stay.

Who: the owner of the venue or of its network (``owns_partner``) or the
platform; checked at every step again. Lock: only the venue's
``PosterConnection`` row; one Poster spot serves one venue — checked before
saving and guaranteed by the ``poster_connection_active_spot_unique`` index.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.integrations.poster.client import PosterClient, PosterError, exchange_code
from apps.integrations.poster.models import POSTER_ACCOUNT_RE, PosterConnection
from apps.partners.networks import owns_partner
from core.i18n import _

SESSION_KEY = "poster_oauth"
START_LIFETIME = timedelta(minutes=15)
# Poster's token lives two years (docs/architecture/POSTER.md); a new login renews it.
TOKEN_LIFETIME = timedelta(days=730)
POSTER_AUTH_URL = "https://joinposter.com/api/auth"


class PosterConnectError(Exception):
    """Why connecting did not happen, in words for the venue's owner."""


def poster_configured() -> bool:
    return bool(
        settings.POSTER_APPLICATION_ID
        and settings.POSTER_APPLICATION_SECRET
        and settings.POSTER_REDIRECT_URI
    )


def may_manage_poster(user, partner_id) -> bool:
    if user is None or not getattr(user, "is_active", False) or partner_id is None:
        return False
    return bool(user.is_superuser or owns_partner(user, partner_id))


def _require_manager(user, partner_id) -> None:
    if not may_manage_poster(user, partner_id):
        raise PosterConnectError(_("Подключать и отключать Poster может владелец заведения."))


def start_connection(*, session, user, partner_id) -> str:
    """Remember the press in the session; the address of Poster's login."""

    if not poster_configured():
        raise PosterConnectError(_("Подключение к Poster на платформе ещё не настроено."))
    _require_manager(user, partner_id)
    session[SESSION_KEY] = {
        "partner_id": str(partner_id),
        "user_id": user.pk,
        "started_at": timezone.now().isoformat(),
    }
    return POSTER_AUTH_URL + "?" + urlencode({
        "application_id": settings.POSTER_APPLICATION_ID,
        "redirect_uri": settings.POSTER_REDIRECT_URI,
        "response_type": "code",
    })


def take_started_connection(*, session, user) -> str:
    """The venue of the press this return answers; the press is spent."""

    entry = session.pop(SESSION_KEY, None)
    if not isinstance(entry, dict):
        raise PosterConnectError(_(
            "Подключение не было начато в Admin или уже завершено. "
            "Начните заново: «Настройки» → «Poster» → «Подключить Poster»."
        ))
    try:
        started = datetime.fromisoformat(entry["started_at"])
    except (KeyError, TypeError, ValueError):
        started = None
    if started is None or timezone.now() - started > START_LIFETIME:
        raise PosterConnectError(_(
            "Вход в Poster длился слишком долго. Начните подключение заново."
        ))
    if entry.get("user_id") != user.pk:
        raise PosterConnectError(_("Подключение начал другой человек. Начните его заново."))
    partner_id = entry.get("partner_id")
    _require_manager(user, partner_id)
    return partner_id


def fetch_spots(token: str) -> list[dict]:
    """The account's working spots (``spots.getSpots``)."""

    with PosterClient(token) as client:
        rows = client.call("spots.getSpots")
    spots = []
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("spot_delete", "0")) == "1":
            continue
        spot_id = int(row["spot_id"])
        spots.append({
            "spot_id": spot_id,
            "name": str(row.get("name") or row.get("spot_name") or spot_id),
            "address": str(row.get("address") or ""),
        })
    return spots


def _spot_taken(connection: PosterConnection, spot_id: int) -> bool:
    return (
        PosterConnection.objects
        .filter(status=PosterConnection.Status.ACTIVE, account_number=connection.account_number,
                spot_id=spot_id)
        .exclude(partner_id=connection.partner_id)
        .exists()
    )


def _spot_name(connection: PosterConnection, spot_id: int) -> str:
    for spot in connection.available_spots or []:
        if spot.get("spot_id") == spot_id:
            return spot.get("name") or str(spot_id)
    return str(spot_id)


def _activate(connection: PosterConnection, spot_id: int) -> None:
    if _spot_taken(connection, spot_id):
        raise PosterConnectError(_(
            "Точка «{spot}» уже подключена к другому заведению Botera.",
            spot=_spot_name(connection, spot_id),
        ))
    connection.spot_id = spot_id
    connection.status = PosterConnection.Status.ACTIVE


def _spot_taken_error() -> PosterConnectError:
    return PosterConnectError(_(
        "Эта точка Poster уже подключена к другому заведению Botera — "
        "или подключение уже идёт. Обновите страницу."
    ))


def finish_connection(*, user, partner_id, account: str, code: str) -> PosterConnection:
    """Trade Poster's code for a token and save the venue's connection."""

    _require_manager(user, partner_id)
    if not POSTER_ACCOUNT_RE.fullmatch(account or ""):
        raise PosterConnectError(_("Poster вернул непонятный аккаунт. Начните подключение заново."))
    if not code:
        raise PosterConnectError(_("Poster не передал код входа. Начните подключение заново."))

    # Network first, outside any transaction.
    try:
        answer = exchange_code(
            account=account,
            code=code,
            application_id=settings.POSTER_APPLICATION_ID,
            application_secret=settings.POSTER_APPLICATION_SECRET,
            redirect_uri=settings.POSTER_REDIRECT_URI,
        )
        token = answer["access_token"]
        spots = fetch_spots(token)
    except PosterError as exc:
        raise PosterConnectError(
            _("Poster не подтвердил вход: {reason}", reason=exc.message),
        ) from None
    if not spots:
        raise PosterConnectError(_("В аккаунте Poster нет ни одной точки."))
    tariff = answer.get("tariff")

    try:
        with transaction.atomic():
            connection = (
                PosterConnection.objects.select_for_update().filter(partner_id=partner_id).first()
                or PosterConnection(partner_id=partner_id)
            )
            connection.account = account
            connection.account_number = str(answer.get("account_number") or "")
            connection.set_access_token(token)
            connection.token_expires_at = timezone.now() + TOKEN_LIFETIME
            if not isinstance(tariff, dict):
                tariff = {} if tariff is None else {"value": tariff}
            connection.tariff = tariff
            connection.available_spots = spots
            connection.connected_by = user
            connection.last_error = ""
            connection.last_error_at = None
            connection.spot_id = None
            connection.status = PosterConnection.Status.PENDING_SPOT
            if len(spots) == 1:
                _activate(connection, spots[0]["spot_id"])
            connection.save()
    except IntegrityError:
        raise _spot_taken_error() from None
    return connection


def choose_spot(*, connection_id, user, spot_id) -> PosterConnection:
    """The owner picks which spot of the account this venue is."""

    try:
        spot_id = int(spot_id)
    except (TypeError, ValueError):
        raise PosterConnectError(_("Такой точки нет в аккаунте Poster.")) from None
    try:
        with transaction.atomic():
            connection = PosterConnection.objects.select_for_update().get(pk=connection_id)
            _require_manager(user, connection.partner_id)
            if connection.status == PosterConnection.Status.DISCONNECTED or not (
                connection.access_token_encrypted
            ):
                raise PosterConnectError(_("Poster отключён. Подключите его заново."))
            if spot_id not in {spot.get("spot_id") for spot in connection.available_spots or []}:
                raise PosterConnectError(_("Такой точки нет в аккаунте Poster."))
            _activate(connection, spot_id)
            connection.save(update_fields=["spot_id", "status", "updated_at"])
    except IntegrityError:
        raise _spot_taken_error() from None
    return connection


def set_menu_mode(*, connection_id, user, mode) -> PosterConnection:
    """«Меню в боте» (owner 2026-11-21, step 7): only Poster's menu where
    orders go to the register, or Botera's own items too."""

    if mode not in PosterConnection.MenuMode.values:
        raise PosterConnectError(_("Такого варианта меню нет."))
    with transaction.atomic():
        connection = PosterConnection.objects.select_for_update().get(pk=connection_id)
        _require_manager(user, connection.partner_id)
        connection.menu_mode = mode
        connection.save(update_fields=["menu_mode", "updated_at"])
    return connection


def disconnect(*, connection_id, user) -> PosterConnection:
    """Orders stop going to Poster; the record and the menu stay."""

    with transaction.atomic():
        connection = PosterConnection.objects.select_for_update().get(pk=connection_id)
        _require_manager(user, connection.partner_id)
        connection.status = PosterConnection.Status.DISCONNECTED
        connection.clear_access_token()
        connection.save(update_fields=[
            "status", "access_token_encrypted", "token_expires_at", "updated_at",
        ])
    return connection
