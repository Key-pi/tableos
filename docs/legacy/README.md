# Исторические документы

Перенесены из корня репозитория 2026-11-14 (решение владельца — порядок в
корне). Это записи своего времени: код и `docs/` важнее их, править их по
существу не нужно, `tests/core/test_docs.py` проверяет в них только ссылки.

| Файл | Что это | Где теперь актуальное |
|---|---|---|
| [TABLEOS_TZ.md](TABLEOS_TZ.md) | Исходное техническое задание TableOS — требования, от которых проект начинался | продукт — [PRODUCT_SPEC.md](../product/PRODUCT_SPEC.md); что из ТЗ куда ушло — [LEGACY_TZ_MIGRATION_MAP.md](../product/LEGACY_TZ_MIGRATION_MAP.md) |
| [PROJECT_CHEATSHEET.md](PROJECT_CHEATSHEET.md) | Старая рабочая шпаргалка с перечнем сделанного до 2026-11-12 | как устроено сейчас — [AS_IS_ARCHITECTURE.md](../architecture/AS_IS_ARCHITECTURE.md); запуск — [DEVELOPMENT.md](../engineering/DEVELOPMENT.md); что сделано по веткам — [REFACTOR_ROADMAP.md](../audits/REFACTOR_ROADMAP.md) |
| [TABLEOS_CODEBASE_AUDIT_MASTER_PROMPT.md](TABLEOS_CODEBASE_AUDIT_MASTER_PROMPT.md) | Задание, по которому в июле 2026 провели первый аудит кода | итоги аудита — [docs/audits/](../audits/); постоянная сверка документов с кодом — `infrastructure/scripts/check_docs.py` |

В строках `docs/product/LEGACY_TZ_MIGRATION_MAP.md` и
`docs/product/PRODUCT_SPEC.md` ссылки вида `TABLEOS_TZ.md:284-295` — это
строки файла [TABLEOS_TZ.md](TABLEOS_TZ.md) в этой папке.
