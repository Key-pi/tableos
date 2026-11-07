"""Links to the Mini App's own files with a version in them.

Telegram keeps styles and scripts in its cache: without a version in the
link, a new ``guest.js`` ran with the old ``guest.css`` after an update
(owner, 2026-11-07). The version is a short hash of the file, so the link
changes exactly when the file does. In production the file name is hashed
too (``ManifestStaticFilesStorage``); the version does no harm there.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=64)
def _hash(path: str, modified: float) -> str:
    with open(path, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()[:10]


def file_version(name: str) -> str:
    found = finders.find(name)
    if not found:
        return ""
    return _hash(found, os.stat(found).st_mtime)


@register.simple_tag
def asset(name: str) -> str:
    url = static(name)
    version = file_version(name)
    return f"{url}?v={version}" if version else url
