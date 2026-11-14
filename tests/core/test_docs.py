"""Documentation stays in step with the code (owner decision, 2026-11-14).

A failure names the document and the line: update the document in the same
commit, or the reference if the code was renamed. The same report without the
test runner: ``python3 infrastructure/scripts/check_docs.py``.
"""

import importlib.util
import pathlib
import tempfile
from unittest import mock

from django.test import SimpleTestCase

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "infrastructure/scripts/check_docs.py"


def _tool():
    spec = importlib.util.spec_from_file_location("botera_check_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _tool()


class DocumentationMatchesCodeTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.files = tool.project_files()

    def test_living_documents_point_to_existing_code(self):
        self.assertEqual(tool.code_references(self.files), [])

    def test_links_and_anchors_resolve(self):
        self.assertEqual(tool.markdown_links(self.files), [])

    def test_models_tasks_commands_modules_handlers_and_settings_are_documented(self):
        self.assertEqual(tool.inventory(self.files), [])

    def test_instructions_for_agents_keep_their_shape(self):
        self.assertEqual(tool.instructions(self.files), [])

    def test_tables_and_test_counts_agree(self):
        self.assertEqual(tool.structure(self.files), [])


class CheckerCatchesDriftTests(SimpleTestCase):
    """The checker itself: a check that silently passes everything is worse than none."""

    def _repo(self, files: dict[str, str]):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        patcher = mock.patch.object(tool, "ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        return tool.project_files()

    def test_a_reference_to_removed_code_or_a_missing_name_is_reported(self):
        files = self._repo({
            "apps/shop/services.py": "def place_order():\n    pass\n",
            "docs/a.md": (
                "`apps/shop/services.py:place_order` works, `apps/shop/gone.py` is gone,\n"
                "`apps/shop/services.py:cancel_order` was never written,\n"
                "`apps/shop/old.py` → `apps/shop/services.py` was a rename,\n"
                "`Europe/Kyiv` and `image/webp` are not paths.\n"
            ),
        })
        self.assertEqual(tool.code_references(files), [
            "docs/a.md:1: нет файла `apps/shop/gone.py`",
            "docs/a.md:2: в `apps/shop/services.py` нет `cancel_order`",
        ])

    def test_dated_records_keep_old_references(self):
        files = self._repo({
            "apps/x.py": "",
            "docs/audits/BASELINE.md": "`apps/removed.py` existed in July.\n",
            "docs/legacy/TABLEOS_TZ.md": "`apps/never_built.py` was only planned.\n",
        })
        self.assertEqual(tool.code_references(files), [])

    def test_a_broken_link_or_anchor_is_reported(self):
        files = self._repo({
            "docs/a.md": "# Остаются открытыми (сводка на 2026-11-14)\n",
            "docs/b.md": (
                "[ok](a.md#остаются-открытыми-сводка-на-2026-11-14)\n"
                "[gone](c.md)\n"
                "[wrong](a.md#нет-такого)\n"
                "[site](https://example.com/x)\n"
            ),
        })
        self.assertEqual(tool.markdown_links(files), [
            "docs/b.md:2: ссылка на несуществующий `c.md`",
            "docs/b.md:3: в `docs/a.md` нет заголовка `#нет-такого`",
        ])

    def test_a_rule_that_matches_no_file_or_a_long_claude_md_is_reported(self):
        files = self._repo({
            "apps/x.py": "",
            "CLAUDE.md": "<!-- note -->\n" + "строка\n" * 201 + "`orphan.md`\n",
            ".claude/rules/orphan.md": '---\npaths:\n  - "apps/nowhere/**"\n---\n# x\n',
            ".claude/rules/unlisted.md": '---\npaths:\n  - "apps/**"\n---\n# x\n',
        })
        self.assertEqual(tool.instructions(files), [
            "CLAUDE.md: 202 строк в контексте, больше 200 — вынести область в .claude/rules/ "
            "или процедуру в навык",
            ".claude/rules/orphan.md: шаблон 'apps/nowhere/**' не совпадает ни с одним файлом",
            ".claude/rules/unlisted.md: файла нет в таблице «Правила по областям кода» CLAUDE.md",
        ])

    def test_test_counts_that_disagree_are_reported(self):
        files = self._repo({
            "docs/engineering/TESTING.md": (
                "На 2026-11-14 тестов **1200**;\n"
                "| 2026-11-14 | шаг | SQLite | весь набор | 1205 тестов OK |\n"
            ),
            "docs/architecture/AS_IS_ARCHITECTURE.md": "- 1200 тестов, зелёные.\n",
        })
        self.assertEqual(tool.structure(files), [
            "число тестов расходится: TESTING.md (шапка) — 1200, AS_IS_ARCHITECTURE.md — 1200, "
            "TESTING.md (последний прогон в журнале) — 1205",
        ])
