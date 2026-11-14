"""The six modules a new venue used to have switched on.

Since PQ-053 (owner, 2026-11-14) a new venue starts with every module off.
Tests written for a working table venue switch these on explicitly.
"""

from bot.services.content import BotContent

TABLE_VENUE_MODULES = {
    "module_menu_enabled": True,
    "module_tables_enabled": True,
    "module_cart_enabled": True,
    "module_orders_enabled": True,
    "module_quick_sale_enabled": True,
    "module_reports_enabled": True,
}


def table_venue_content() -> BotContent:
    """``BotContent.defaults()`` with the table venue's modules on."""

    content = BotContent.defaults()
    for name, value in TABLE_VENUE_MODULES.items():
        setattr(content, name, value)
    return content
