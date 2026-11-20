"""Requests to the Poster API (ADR-0010; plan — docs/architecture/POSTER.md).

Only network code lives here: no database, no transactions. Callers run it
from Celery tasks, outside an open transaction (AGENTS.md).

Poster answers ``{"response": …}`` or ``{"error": …}`` — the error either as
a number with ``message`` next to it or as an object with ``code`` and
``message`` (both seen on the trial account, step 0), sometimes with HTTP 200.
Errors never carry the token, the application secret or an OAuth code: the
token travels in the query string, and Poster may echo what it was sent.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

API_BASE = "https://joinposter.com/api/"
ACCOUNT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})$")
METHOD_RE = re.compile(r"^[A-Za-z]+\.[A-Za-z]+$")
DEFAULT_TIMEOUT = 15.0
USER_AGENT = "Botera Shop"
TEMPORARY_STATUSES = {429}


def _quiet_http_loggers() -> None:
    """httpx logs each request's full URL at INFO, with the token in it.
    ``core/logging/config.py`` raises these loggers to WARNING; this repeats
    it for processes that set up logging their own way (a Celery worker)."""

    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)


_quiet_http_loggers()


class PosterError(Exception):
    """A Poster request that did not give an answer.

    ``temporary`` — worth repeating later (network, timeout, Poster busy);
    otherwise Poster refused the request and repeating changes nothing.
    """

    def __init__(self, message: str, *, code: Any = None, http_status: int | None = None,
                 temporary: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.temporary = temporary

    def __repr__(self) -> str:
        return (f"PosterError(code={self.code!r}, http_status={self.http_status!r}, "
                f"temporary={self.temporary!r}, message={self.message!r})")


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    """Hide secrets, also as they look inside a URL or a form."""

    for secret in secrets:
        if secret:
            for form in {secret, quote(secret, safe=""), quote_plus(secret)}:
                text = text.replace(form, "***")
    return text


def _temporary_status(status: int) -> bool:
    return status >= 500 or status in TEMPORARY_STATUSES


def _error_from_payload(payload: Any, status: int, secrets: tuple[str, ...]) -> PosterError | None:
    if not isinstance(payload, dict) or "error" not in payload:
        return None
    error = payload["error"]
    if isinstance(error, dict):
        code, message = error.get("code"), error.get("message", "")
    else:
        code, message = error, payload.get("message", "")
    return PosterError(
        _scrub(str(message or f"Poster error {code}"), secrets),
        code=code, http_status=status, temporary=_temporary_status(status),
    )


def _send(client: httpx.Client, request: httpx.Request, secrets: tuple[str, ...]) -> Any:
    """Send and return the decoded JSON; every failure is a PosterError."""

    try:
        answer = client.send(request)
    except httpx.TimeoutException:
        raise PosterError("Poster did not answer in time", temporary=True) from None
    except httpx.TransportError as exc:
        raise PosterError(
            f"Poster is unreachable: {type(exc).__name__}", temporary=True,
        ) from None
    status = answer.status_code
    try:
        payload = answer.json()
    except ValueError:
        raise PosterError(
            f"Poster answered HTTP {status} without JSON",
            http_status=status, temporary=_temporary_status(status),
        ) from None
    error = _error_from_payload(payload, status, secrets)
    if error is not None:
        raise error
    if status >= 400:
        raise PosterError(
            f"Poster answered HTTP {status}", http_status=status,
            temporary=_temporary_status(status),
        )
    return payload


class PosterClient:
    """One venue's calls to the Poster API with its access token."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._token = token
        self._http = httpx.Client(
            transport=transport, timeout=timeout, headers={"User-Agent": USER_AGENT},
        )

    def __enter__(self) -> PosterClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        """``GET`` (or ``POST`` with a JSON ``body``) ``<API>/<method>``;
        returns what Poster put in ``response``."""

        if not METHOD_RE.fullmatch(method or ""):
            raise ValueError(f"Not a Poster API method: {method!r}")
        query = {"token": self._token, "format": "json"}
        query.update({key: value for key, value in (params or {}).items() if value is not None})
        request = self._http.build_request(
            "POST" if body is not None else "GET", API_BASE + method, params=query, json=body,
        )
        payload = _send(self._http, request, (self._token,))
        if not isinstance(payload, dict) or "response" not in payload:
            raise PosterError("Poster answered without «response»")
        return payload["response"]


def exchange_code(*, account: str, code: str, application_id: str, application_secret: str,
                  redirect_uri: str, transport: httpx.BaseTransport | None = None,
                  timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Trade the OAuth ``code`` for a venue's access token.

    ``account`` comes from the callback's query string; it names the host the
    secret is sent to, so anything but a plain Poster login is refused.
    """

    if not ACCOUNT_RE.fullmatch(account or ""):
        raise ValueError("Not a Poster account login")
    secrets = (application_secret, code)
    with httpx.Client(transport=transport, timeout=timeout,
                      headers={"User-Agent": USER_AGENT}) as http:
        request = http.build_request(
            "POST",
            f"https://{account}.joinposter.com/api/v2/auth/access_token",
            data={
                "application_id": application_id,
                "application_secret": application_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        payload = _send(http, request, secrets)
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise PosterError("Poster gave no access token")
    return payload
