"""Photos of menu items are stored small (PQ-048).

An upload of up to ``MENU_PHOTO_MAX_BYTES`` is turned into WebP, its long side
at most ``MAX_SIDE`` px (about 100–200 KB): a menu of 30 items opens fast on
mobile internet and the server sends little. The original is not kept.
"""

from __future__ import annotations

from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_SIDE = 1200
WEBP_QUALITY = 80


def compress_menu_photo(source) -> ContentFile:
    """WebP of the photo, turned upright by its EXIF and made at most MAX_SIDE."""

    if hasattr(source, "seek"):
        source.seek(0)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        image = image.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, "WEBP", quality=WEBP_QUALITY, method=4)
    return ContentFile(buffer.getvalue(), name="photo.webp")
