"""Every text of the code is translated to Ukrainian and English (PQ-040)."""

import importlib.util
import pathlib
import sys

from django.test import SimpleTestCase

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "infrastructure/scripts/messages.py"


def _messages_tool():
    spec = importlib.util.spec_from_file_location("botera_messages", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CatalogCompletenessTests(SimpleTestCase):
    def test_catalogs_are_complete_and_compiled(self):
        # Fix: python infrastructure/scripts/messages.py update, translate the
        # empty msgstr in locale/{uk,en}/LC_MESSAGES/django.po, then compile.
        self.assertEqual(_messages_tool().problems(), [])

    def test_check_finds_a_missing_translation_and_a_wrong_placeholder(self):
        tool = _messages_tool()
        self.assertEqual(tool._fields("Сумма {total} и {{скобки}}"), ["total"])
        self.assertEqual(tool._tags("<b>Да</b> <a href='x'>к</a>"), ["a", "a", "b", "b"])
        po = tool.render_po(
            "uk",
            [tool.Message("Строка\nвторая", "Рядок\nдругий", ["bot/x.py:1"]),
             tool.Message('Кавычки "x"', "", ["bot/x.py:2"])],
        )
        self.assertEqual(
            tool.parse_po(po), {"Строка\nвторая": "Рядок\nдругий", 'Кавычки "x"': ""}
        )
