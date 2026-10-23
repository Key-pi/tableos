"""HTML escaping for Telegram messages sent with ``ParseMode.HTML``."""

import html


def escape_html(value) -> str:
    """Escape a dynamic value (venue, menu, guest or staff text) for HTML mode.

    Telegram rejects messages with a bare ``&``, ``<`` or ``>`` outside a tag,
    so every value that does not come from code must pass through here before
    it is placed into message text. Keyboard button labels are plain text and
    must not be escaped.
    """

    if value is None:
        return ""
    return html.escape(str(value), quote=False)
