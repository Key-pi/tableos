"""Uploaded files (photos of menu items, PQ-048).

WhiteNoise serves only the static files collected at start, so uploads are
served here: only files inside ``MEDIA_ROOT``, with a long cache — every
upload gets a new name. A proxy in front (Caddy, nginx) may serve
``/media/`` from the same folder instead (DEPLOYMENT.md).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpRequest
from django.utils._os import safe_join
from django.views.decorators.http import require_GET

CACHE_SECONDS = 60 * 60 * 24 * 30
# The photo formats an upload may have, with their types. Not asked of
# ``mimetypes``: Python 3.12 (the Docker image) knows .webp only as a
# "non-strict" type, so without /etc/mime.types every compressed photo
# answered 404 (2026-11-06).
PHOTO_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@require_GET
def serve_media(request: HttpRequest, path: str) -> FileResponse:
    try:
        full = Path(safe_join(settings.MEDIA_ROOT, path))
    except (SuspiciousFileOperation, ValueError) as exc:  # outside MEDIA_ROOT
        raise Http404 from exc
    if not full.is_file():
        raise Http404
    content_type = PHOTO_TYPES.get(full.suffix.lower())
    if content_type is None:
        raise Http404  # only photos are uploaded; nothing else is handed out
    response = FileResponse(full.open("rb"), content_type=content_type)
    response["Cache-Control"] = f"public, max-age={CACHE_SECONDS}, immutable"
    response["Content-Disposition"] = "inline"
    return response
