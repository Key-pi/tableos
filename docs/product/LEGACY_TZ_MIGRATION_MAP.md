# Migration map: legacy root documents to canonical documentation

**Status:** audit governance map; legacy files remain preserved during audit mode.  
**Rule:** no product meaning was discarded or silently rewritten. Current behavior is determined by executable code; desired behavior remains a product contract only when marked as such.

## Source hierarchy after canonicalisation

| Source | Role after this audit | Canonical destination |
| --- | --- | --- |
| `README.md` | Short onboarding only; not an implementation-status authority | `docs/engineering/DEVELOPMENT.md`, `docs/engineering/TESTING.md`, architecture docs |
| `PROJECT_CHEATSHEET.md` | Legacy operational notes; commands and baseline claims require verification | `docs/audits/BASELINE.md`, `docs/engineering/*`, `docs/architecture/BACKGROUND_JOBS.md` |
| `TABLEOS_TZ.md` | Preserved legacy product/design source and historical evidence | `docs/product/*`, architecture docs, ADRs and audit reports |
| Executable code/models/migrations/tests | Source of truth for current behavior | `docs/architecture/AS_IS_ARCHITECTURE.md`, `docs/audits/ARCHITECTURE_AUDIT.md` |

Do not delete or rename the legacy files in an implementation batch merely because their content has been mapped. Any removal needs a separate link-impact review.

## `TABLEOS_TZ.md` section map

| Legacy section | Canonical destination | Treatment |
| --- | --- | --- |
| 1. Краткая суть проекта | [PRODUCT_SPEC §1](PRODUCT_SPEC.md#1-назначение-и-границы-продукта) | Product positioning retained |
| 2. Продуктовая цель | [PRODUCT_SPEC §§1, 5](PRODUCT_SPEC.md#1-назначение-и-границы-продукта) | Desired outcomes and journeys retained |
| 3. Основные роли (3.1–3.7) | [PRODUCT_SPEC §2](PRODUCT_SPEC.md#2-роли-и-контексты-доступа), [OPEN_PRODUCT_QUESTIONS](OPEN_PRODUCT_QUESTIONS.md) | Role intent retained; exact authority matrix remains open |
| 4. Термины | [PRODUCT_SPEC §§1, 5, 6](PRODUCT_SPEC.md#1-назначение-и-границы-продукта) | Domain vocabulary retained; architecture glossary belongs in AS-IS/target docs |
| 5. Текущее состояние проекта | [FEATURE_MATRIX](FEATURE_MATRIX.md), `docs/architecture/AS_IS_ARCHITECTURE.md` | Reclassified as code-verified current state, not asserted from prose |
| 6. Принципы продукта (6.1–6.6) | [PRODUCT_SPEC §§2–4](PRODUCT_SPEC.md#2-роли-и-контексты-доступа) | Preserved as invariants; divergence evidence recorded below |
| 7.1 Базовый модуль/profile | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Capability and loyalty-only journey retained |
| 7.2 Меню | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Menu browse/order capability retained |
| 7.3 Столы и QR-сессии | [PRODUCT_SPEC §§4–6](PRODUCT_SPEC.md#4-capability-model), [OPEN_PRODUCT_QUESTIONS PQ-007/PQ-008](OPEN_PRODUCT_QUESTIONS.md) | Target session invariant retained; race identified |
| 7.4 Корзина и заказы | [PRODUCT_SPEC §§4–6](PRODUCT_SPEC.md#4-capability-model), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Main flow retained; state/concurrency audit cross-linked |
| 7.5 Мой стол | [PRODUCT_SPEC §5.3](PRODUCT_SPEC.md#53-table-order) | Guest journey retained; detailed screen copy remains legacy source until UX work |
| 7.6 Запрос счёта | [PRODUCT_SPEC §5.4](PRODUCT_SPEC.md#54-billing-and-payment) | Personal/shared/custom request contract retained |
| 7.7 Счета и оплаты | [PRODUCT_SPEC §§3, 5.4, 6](PRODUCT_SPEC.md#3-непереговорные-продуктовые-инварианты) | Financial invariants retained; allocation defect recorded |
| 7.8 Вызов персонала | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Capability retained; cooldown needs hardening |
| 7.9 Бонусы и лояльность | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model), [OPEN_PRODUCT_QUESTIONS PQ-002](OPEN_PRODUCT_QUESTIONS.md) | Event model retained; award timing remains owner decision |
| 7.10 Быстрые продажи | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model) | Staff capability retained |
| 7.11 Staff-бот | [PRODUCT_SPEC §§2, 5.5](PRODUCT_SPEC.md#2-роли-и-контексты-доступа), [OPEN_PRODUCT_QUESTIONS PQ-004/PQ-005](OPEN_PRODUCT_QUESTIONS.md) | Product role/capability contract retained; active-user gap explicit |
| 7.12 Отчёт дня | [PRODUCT_SPEC §§4–5](PRODUCT_SPEC.md#4-capability-model), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Current on-demand MVP vs future reporting preserved |
| 7.13 Рассылки | [PRODUCT_SPEC §5.6](PRODUCT_SPEC.md#56-broadcasts-and-notifications), [OPEN_PRODUCT_QUESTIONS PQ-014/PQ-015](OPEN_PRODUCT_QUESTIONS.md) | Delivery promise must be chosen before redesign |
| 8. Настройки бота (8.1–8.3) | [PRODUCT_SPEC §7](PRODUCT_SPEC.md#7-контент-и-конфигурация-контракт), [FEATURE_MATRIX](FEATURE_MATRIX.md) | Capability/template requirements retained; label collision not hidden |
| 9. Back-office/admin (9.1–9.10) | [PRODUCT_SPEC §§2–3](PRODUCT_SPEC.md#2-роли-и-контексты-доступа), [OPEN_PRODUCT_QUESTIONS PQ-017/PQ-019](OPEN_PRODUCT_QUESTIONS.md), `docs/architecture/TENANCY_AND_PERMISSIONS.md` | Desired scoped/safe admin contract retained; current bypass paths audited |
| 10. Telegram runtime | [PRODUCT_SPEC §§3, 5.5](PRODUCT_SPEC.md#3-непереговорные-продуктовые-инварианты), `docs/architecture/BOT_RUNTIME.md` | Polling current; webhook explicitly future scope |
| 11. Уведомления и фоновые задачи | [PRODUCT_SPEC §§3, 5.6](PRODUCT_SPEC.md#3-непереговорные-продуктовые-инварианты), `docs/architecture/BACKGROUND_JOBS.md` | `on_commit` intent retained; transaction/duplicate-delivery defect explicit |
| 12. Данные и статусы (12.1–12.7) | [PRODUCT_SPEC §6](PRODUCT_SPEC.md#6-state-contracts), `docs/architecture/AS_IS_ARCHITECTURE.md` | Target state contracts separated from observed transitions |
| 13. Целевые сценарии (13.1–13.6) | [PRODUCT_SPEC §5](PRODUCT_SPEC.md#5-core-user-journeys) | All major journeys retained in concise canonical form |
| 14. MVP acceptance criteria (14.1–14.9) | [FEATURE_MATRIX](FEATURE_MATRIX.md), `docs/audits/REFACTOR_ROADMAP.md` | Criteria converted to status matrix and future characterization tests |
| 15. Будущие модули (15.1–15.6) | [PRODUCT_SPEC §9](PRODUCT_SPEC.md#9-future-scope-and-exclusions), [OPEN_PRODUCT_QUESTIONS](OPEN_PRODUCT_QUESTIONS.md) | Explicitly future, not claimed as current features |
| 16. Риски и решения (16.2–16.6) | [PRODUCT_SPEC §3](PRODUCT_SPEC.md#3-непереговорные-продуктовые-инварианты), ADRs | Retained as design principles |
| 17. Рекомендуемая очередность | `docs/audits/REFACTOR_ROADMAP.md` | Replaced by evidence-based reversible batches, not product prose ordering |
| 18. Night Owl target configuration | [PRODUCT_SPEC §4](PRODUCT_SPEC.md#4-capability-model) | Retained as example/fixture intent; not a universal default |
| 19. Что нужно уточнить | [OPEN_PRODUCT_QUESTIONS](OPEN_PRODUCT_QUESTIONS.md) | All legacy questions preserved and expanded with audit decisions |
| 20. Краткое резюме целевой логики | [PRODUCT_SPEC §§1, 5](PRODUCT_SPEC.md#1-назначение-и-границы-продукта) | Target distinction between loyalty-only and table-order journeys retained |
| 21. Карта текущей реализации (21.1–21.10) | [FEATURE_MATRIX](FEATURE_MATRIX.md), `docs/architecture/AS_IS_ARCHITECTURE.md`, `docs/audits/CODEBASE_INVENTORY.md` | Replaced by source-verified mapping; every claim now needs code evidence |

## Legacy-document contradictions and evidence

| Legacy assertion / location | Code evidence | Classification | Canonical handling |
| --- | --- | --- | --- |
| Staff requires active `User.is_active=True` (`TABLEOS_TZ.md:284-295`, 1144 onward) | `apps/employees/selectors.py:11-17` filters only active `EmployeeProfile`; `bot/handlers/staff.py:842-858` uses that selector | CODE_IS_BEHIND_SPEC | Product invariant is retained in PRODUCT_SPEC §3; audit/roadmap must harden it |
| Suspended partner must not serve real flows (`TABLEOS_TZ.md:1880-1892`) | `bot/services/runtime.py:21-79` checks status during startup; `bot/services/context.py:5-7` returns partner without a current-status gate | CODE_IS_BEHIND_SPEC / product decision | PQ-013 defines required suspension semantics |
| Exactly one active table session per guest/partner (`TABLEOS_TZ.md:1900-1907`) | `apps/tables/models.py:62-64` has no constraint; `apps/tables/services.py:44-91` locks a table, not guest/session row | DEFECT | Retained as invariant; implementation needs PostgreSQL-safe design |
| One order item cannot enter two open/paid bills (`TABLEOS_TZ.md:763-947`, 2185 onward) | `apps/billing/services.py:252-307` performs check then `bulk_create` without locking allocations/DB unique guard | DEFECT | Retained as money invariant; roadmap requires characterization first |
| Staff/order transitions have safe lifecycle (`TABLEOS_TZ.md:531-691`, 2091 onward) | `apps/orders/services.py:185-232` receives mutable caller object and does not refresh with `select_for_update()` | DEFECT | State contract remains desired; current callback race is audited |
| Order-completed bonuses operate (`TABLEOS_TZ.md:1012-1097`, 21.7) | `apps/bonuses/services.py:33-77` supports generic event; table activation calls `VISIT`, and no production completion caller was found | CODE_IS_BEHIND_SPEC | PQ-002 blocks choosing a trigger without owner approval |
| Labels must not break handler filtering (`TABLEOS_TZ.md:1512-1537`) | `bot/filters/content.py:10-21` matches raw reply text; `PartnerBotSettings.clean` validates module dependencies but not collisions | DEFECT / CODE_IS_BEHIND_SPEC | PQ-016 sets decision options |
| Telegram calls are outside DB transactions (`TABLEOS_TZ.md:1852-1877`) | `apps/notifications/services.py:440-503` marks broadcast sender atomic and sends via Telegram at line 503 | DEFECT | Product contract remains; architecture batch must separate delivery phases |
| Admin uses safe actions instead of breaking transitions (`TABLEOS_TZ.md:1752-1808`) | `apps/orders/admin.py`, `apps/tables/admin.py`, `apps/billing/admin.py` expose mutable operational fields; billing request actions use direct `queryset.update` | CODE_IS_BEHIND_SPEC | PQ-017 records governance choice; no silent admin behavior change |
| Delivery/pickup flags mean usable journeys (`TABLEOS_TZ.md:1474-1511`, 2220 onward) | `apps/partners/models.py:258-278` validates flags, but current handlers/services do not provide those full journeys | CODE_IS_BEHIND_SPEC / FUTURE_SCOPE | Feature matrix marks flags as groundwork only |

## `README.md` and `PROJECT_CHEATSHEET.md` reconciliation

| Document item | Evidence | Classification | Canonical destination / required safe documentation correction |
| --- | --- | --- | --- |
| CHEATSHEET said `manage.py test: 66 tests OK` and Ruff fully passed at audit baseline | Verified baseline in `docs/audits/BASELINE.md`: 139 tests pass; `ruff check --no-cache .` has 21 pre-existing violations | DOC_DRIFT | Root cheatsheet now carries a status banner/current baseline and links to `docs/audits/BASELINE.md`; original claim remains audit evidence |
| CHEATSHEET task list omitted scheduled broadcast processing at audit baseline | `apps/notifications/tasks.py:79-82` registers `process_scheduled_broadcast_campaigns_task` | DOC_DRIFT | Root cheatsheet and `docs/architecture/BACKGROUND_JOBS.md` now list the task |
| CHEATSHEET contained machine-specific `/Users/danil/projects/tableos/...` Markdown links at audit baseline | Baseline root document scan | DOC_DRIFT | Replaced with repository-relative links; scan is recorded in audit summary |
| Root docs included demo passwords/tokens as command examples at audit baseline | Original `README.md` and `PROJECT_CHEATSHEET.md` seed/bootstrap examples | DOCUMENTATION_HYGIENE | Canonical onboarding now uses placeholders/local environment values; no real/production credentials are documented |
| README offered bare `python`/`celery` commands while verified environment uses project `.venv` and optional `DJANGO_USE_SQLITE=true` | `docs/audits/BASELINE.md`, `pyproject.toml`, actual verified command set | DOC_DRIFT / operational ambiguity | Canonical `docs/engineering/DEVELOPMENT.md` and `TESTING.md` now contain verified commands |

## Documentation-only migration executed in this audit

1. Created canonical product, architecture, engineering and audit docs.
2. Shortened `README.md` to onboarding plus canonical links.
3. Added legacy/status banners to `TABLEOS_TZ.md` and `PROJECT_CHEATSHEET.md` while preserving their substantive historical content.
4. Corrected mechanical local links, stale baseline statements and the task list with this audit as evidence.
5. Checked local Markdown targets. No legacy root file was deleted or renamed.
