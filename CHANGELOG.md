## 0.7.1rc14 — 2026-06-24 — source-evidence reconciliation

- Добавлен fallback профиля из явного токена исходного описания при пустой
  структурированной колонке.
- Добавлено консервативное распознавание явно написанного материала без
  переводов, химических символов и синонимов.
- Исправлено ошибочное распознавание чистого размерного блока `4х4` как марки.
- Generic-парсер сохраняет полную исходную строку, заголовки и отображение
  `заголовок → значение`, а не только первые девять ячеек.
- LLM-схема расширена полевыми причинами и точным source evidence.
- Изменение LLM принудительно переводится в REVIEW, если evidence отсутствует,
  не найдено в источнике или не подтверждает предложенное значение.
- Добавлен детерминированный слой применения только подтверждённых
  `PROPOSE_CHANGE`.
- Поиск и XLSX перенесены после LLM reconciliation.
- Добавлены `source_audit.jsonl` и `llm_application_audit.json`.
- Формат десяти колонок сохранён.
- Общий лимит LLM-кандидатов не возвращён; обработка остаётся пакетной по 10.

## 0.7.1rc13 — 2026-06-24 — исправление staging-fixture

- В staging-копию установщика добавлен каталог `pilot`.
- Перед запуском pytest проверяется наличие `pilot/pilot_gold_v0_7_1_rc3.jsonl`.
- Из staging удаляются `__pycache__` и `.pytest_cache`, чтобы исключить влияние старого bytecode.
- Исправлена причина `FileNotFoundError` в `test_rc3_gold_uses_circle_profile`.
- Функциональный код универсального поиска относительно RC12 не изменён.
- Лимит LLM в 25 строк не возвращён: сохраняется пакетная обработка всех кандидатов.

## 0.7.1rc12 — 2026-06-24 — QA preflight and stale-test fix

- Fixed the stale RC10 assertion that expected `Квадрат 100` to be rewritten
  to `Квадрат ф100`; RC11+ parses the generic mask directly.
- Added staging preflight before any production files are changed.
- Added package hash verification before installation.
- Added direct console diagnostics with stdout/stderr tails on failure.
- Preserved the RC11 generic search implementation without functional changes.

## 0.7.1rc11 — 2026-06-23 — generic optional search mask

- Replaced diameter-only full search with optional name/grade/dim1/dim2/dim3 mask.
- Missing query fields are wildcards and never raise an exception.
- Added one-, two- and three-dimensional parsing with x/х/×/* separators.
- Added inventory-aware grade extraction before dimension parsing.
- Preserved grades containing digits and Cyrillic Х, including 12Х18Н10Т.
- Added whitespace, tab and non-breaking-space normalization.
- Ignored package weights and quantities while detecting dimensions.
- Unknown requests such as `Стол 1000х1500` return an empty search XLSX and
  `Позиции не найдены` instead of failing.
- Preserved exact-first/nearest-size ranking and round-bar search equivalence.
- Retained RC9 full LLM candidate coverage in sequential batches of 10.

## 0.7.1rc10 — 2026-06-23 — optional grade in full search

- Search grade is now optional when omitted by the user.
- `Квадрат 100` searches every `КВАДРАТ` grade with side 100 ranked first.
- Explicit `БрБ2` aliases continue to filter by `БРБ2`.
- Explicit numeric grades such as `ст.20` and `марка 20` are supported.
- Added direct search and end-to-end `run-folder` regression tests.
- Retained RC9 unlimited LLM candidate coverage and RC8 square shorthand.

## 0.7.1rc9 — 2026-06-23 — unlimited REVIEW coverage in demo

- Removed the default 25-row cap from `run-folder` and `START_DEMO.cmd`.
- Every REVIEW candidate is processed by LLM by default.
- Candidates are sent sequentially in batches of 10; batch size remains bounded
  to 1–10 per API request.
- Mandatory live LLM now requires full candidate coverage before final XLSX
  files are accepted.
- Added `llm_batches.json`, per-batch status, cumulative tokens and cost.
- A positive `--llm-max-cases` remains available only for explicit diagnostics;
  default `0` means all candidates.
- Preserved RC8 square query normalization and CMD BOM fix.

# CHANGELOG

## 0.7.1rc8 — 2026-06-23 — одиночная сторона квадрата в поиске

- Запрос `Квадрат 100` трактует `100` как сторону квадрата (`Размер 1`).
- Внутренняя совместимость: запрос преобразуется в `Квадрат ф100`.
- Исходная заявка сохраняется в имени XLSX и отчёте.
- `Квадрат ст.20 100` трактуется как марка 20 и сторона 100.
- Два немаркированных числа не интерпретируются автоматически.
- Добавлены unit-тесты нормализации поискового запроса.
- `START_DEMO.cmd` пересохранён как UTF-8 без BOM.

## 0.7.1rc7 — 2026-06-23 — обязательный LLM, full query и прайс SupplierDelta

- Добавлена загрузка локального `.env` без вывода секретов.
- `START_DEMO.cmd` переведён на полную заявку `пруток БрБ2 ф20`.
- При обязательном live LLM отсутствие ключа или ошибка API блокируют финальный XLSX.
- Добавлен отчёт `llm_required / llm_requirement_met`.
- Generic parser распознаёт `Вид проката`, `Диаметр`, `Размеры мм.`, `Остаток в кг`.
- Исправлены строки SupplierDelta: `ЭИ868(ХН60ВТ) | круг | ф105 | 360мм`
  преобразуется в `КРУГ | ХН60ВТ | 105 | 360`.
- Поддержаны поздние заголовки и повторные схемы внутри workbook.
- Размероподобное значение больше не публикуется как профиль.
- При пустом исходном профиле выводится `НЕ УКАЗАН` и строка направляется в REVIEW.
- LLM сначала получает спорные строки неизвестного поставщика с `source_columns`.
- Добавлена инструкция модели не сдвигать явные роли исходных колонок.
- Responses API вызывается с `store=false`.
- Полный поиск создаёт `Общая.xlsx` и отдельный ранжированный XLSX:
  точное совпадение, затем ближайшие размеры.
- В full-query режиме нет искусственного удвоения ПРУТОК/КРУГ.
- Добавлены тесты `.env`, нового прайса, позднего заголовка, полного поиска и mandatory LLM.
- Live API на ключе пользователя требует проверки после установки.

## 0.7.1rc5 — 2026-06-22 — поисковое представление ПРУТОК/КРУГ

- Основная таблица из 10 колонок не изменяется и не дублируется.
- Отдельная таблица `search_brb2_f20.xlsx` показывает каждое найденное
  складское предложение под двумя эквивалентными поисковыми профилями:
  `ПРУТОК` и `КРУГ`.
- Alias-строка явно помечается в комментарии как поисковое представление и
  не считается дополнительным складским остатком.
- При открытии поисковой таблицы сохранён фильтр `Размер 1 = 20 мм`.
  Ближайшие размеры остаются в файле и появляются после снятия фильтра.
- Контрольный прогон трёх прайсов: 4 371 исходная позиция, 152 исходных
  совпадения, 304 строки поискового представления, по 152 строки для
  `ПРУТОК` и `КРУГ`; при фильтре Ø20 видны 8 строк.
- Добавлена строгая проверка пар ПРУТОК/КРУГ, alias-маркеров и сохранённого
  Excel-фильтра.
- QA: `164 passed, 1 skipped`.
- Реальный прогон трёх предоставленных файлов: `VERIFIED`.

## 0.7.1rc4 — 2026-06-22 — КРУГ/ПРУТОК в поиске

- Исправлено правило тестового задания: `КРУГ` и `ПРУТОК` образуют одну
  поисковую группу.
- Запрос `пруток БрБ2 ф20` теперь рассматривает позиции профилей `ПРУТОК`
  и `КРУГ`.
- Исходный профиль в основной таблице не переписывается: трассируемость
  поставщика сохраняется.
- Для `КРУГ` и `ПРУТОК` унифицировано размерное правило: `Размер 1` —
  диаметр, `Размер 2` — явно указанная длина; поддержан формат
  `Круг д240×115`.
- При одинаковом размере профиль, буквально указанный в запросе, показывается
  первым; второй профиль остаётся в той же группе совпадений.
- Остальные профили (`ТРУБА`, `ЛЕНТА` и т. п.) не включаются.
- Общее количество результатов контрольного поиска должно быть пересчитано на
  рабочей Windows-машине после установки обновления.
- Удалён fallback, который при отсутствии спорных строк отправлял в LLM
  первые обычные позиции. Теперь при отсутствии REVIEW-кандидатов внешний
  вызов не выполняется (`NOT_NEEDED`, 0 токенов).
- Добавлены регрессионные тесты для обоих направлений поиска.
- QA исходного кода: `161 passed, 1 skipped`.

## 0.7.1rc3 — documentation closure — 2026-06-20

Documentation-only closure; executable version remains `0.7.1rc3`.

- Updated current README and `.env.example`.
- Marked live LLM/replay as `VERIFIED`.
- Recorded corrected control gold: `CTRL-003` expects `КРУГ`, not `ПРУТОК`.
- Recorded final quality: `15/15`, all checked fields `1.0`.
- Added final test-assignment passport `2.2`.
- Added current readiness, acceptance, limitations and GitHub handoff guides.
- Separated current authoritative documents from historical versioned records.
- Recorded that the second computer is not part of the delivery critical path.
- Recorded that the unknown-price run is an external acceptance step pending a
  customer-provided file or online demonstration.
- Previous delivery ZIP built before this refresh must be rebuilt before sending.

## 0.7.1rc3 — 2026-06-19 — profile taxonomy and safe LLM

- Added confirmed rule `КРУГ ≠ ПРУТОК`.
- Added LLM access policy and safe REVIEW-only application.
- Added `OPENAI_API_KEY` as the primary key variable.
- Added idempotent live/replay reconciliation.
- Added local zero-API revalidation.
- Fixed control gold `CTRL-003`.
- QA: `155 passed, 1 skipped`.
- LLM offline audit: `19/19 VERIFIED`.
- Live/replay after revalidation: `VERIFIED`.

## Historical releases

The entries below describe earlier release states. Old `NOT_VERIFIED`,
`BLOCKED`, test counts and key names are historical and do not define the
current status of `0.7.1rc4`.

## 0.7.1rc2
- Исправлен экспорт: основной XLSX содержит один лист и 10 колонок.
- Поиск `БрБ2 ф20` вынесен в отдельные XLSX и JSON.
- Исправлена ведущая толщина `т.` для листов и полос.
- Диапазоны и альтернативы размеров сохраняются без усечения.
- Исправлена потеря третьего размера полосы.
- Исправлен приоритет `БРБ2` для `Alloy 25 / C17200 / CuBe2` при конфликтующем grade hint.
- Несколько размерных наборов переводятся в REVIEW и не сливаются в ложный диапазон.
- Четырёхкомпонентные размеры сохраняют первые три компонента и отмечают четвёртый в комментарии.
- Фактический прогон трёх прайсов: 4 371 позиция.
- Acceptance QA: 11/11 VERIFIED.
- QA: 144 passed, 1 skipped.
- Live LLM и неизвестный прайс: NOT_VERIFIED/BLOCKED.

## 0.7.1rc1
- Граница тестового задания отделена от полного коммерческого проекта.
- Добавлена команда `test-assignment`.
- Добавлен XLSX ровно из 10 колонок задания.
- Добавлены отчёты времени, скорости, REVIEW, поиска и LLM.
- Generic parser выбирает цену с НДС при наличии двух цен.
- Добавлен `llm-offline-audit`: 14 офлайн-проверок.
- Усилена защита от prompt injection в supplier/description.
- Replay-файлы проходят Pydantic-валидацию и проверку токенов.
- Добавлены программные пороги принятия live LLM.
- QA сборки: 133 passed, 1 skipped.
- Live LLM и неизвестный прайс: NOT_VERIFIED/BLOCKED.

## 0.7.0rc2
- Исправлен `WinError 32` в Document/PDF mock smoke на Windows.
- SQLAlchemy engine освобождается до удаления временной SQLite БД.
- Добавлен subprocess-регрессионный тест smoke.
- QA: 122 passed, 1 skipped.
- Функциональность документов и миграция 0003 без изменений.
- Live LLM/SMTP/IMAP/amoCRM: NOT_VERIFIED.

## 0.7.0rc1
- КП и счёт: детерминированные расчёты, версии и аудит.
- Миграция 0003 для документов и обновлений бюджета.
- PDF тестового шаблона с SHA-256.
- Mock-обновление бюджета amoCRM.
- API endpoints `/documents`.
- Накопительный реестр проверок.
- 121 passed, 1 skipped.
- Live LLM/SMTP/IMAP/amoCRM: NOT_VERIFIED.

# Changelog

## 0.6.0rc1 — Mail workflow and cumulative verification register

- Added persistent mail requests, messages, attachments and processing attempts.
- Added Alembic migration `0002`.
- Added mock SMTP, local `.eml` replay, Message-ID/References linking and request-token fallback.
- Added attachment size, extension and signature checks with quarantine.
- Added retry and DEAD_LETTER handling.
- Added mail API/CLI and mock smoke.
- Added cumulative project verification register.
- Live SMTP, IMAP and LLM E2E remain `NOT_VERIFIED`.

## 0.5.0rc1 — Persistent database and backend API

- Added SQLAlchemy models and Alembic migration `0001`.
- Added SQLite local storage with PostgreSQL-compatible schema.
- Added idempotent pilot import with provenance and audit.
- Added local FastAPI endpoints for health, search, REVIEW and LLM run audit.
- Added explicit review decision application; automatic application remains disabled.
- Added five database/API regression tests and real 4,371-offer import smoke.
- Live LLM E2E remains `NOT_VERIFIED`.

## 0.4.0rc2 — Safe LLM pipeline

- Добавлен безопасный LLM-контур `llm-safe`.
- Добавлены mock-режим, строгая схема, guardrails, audit и fingerprint.
- Реальные API-вызовы требуют явного `--confirm-live-api`.
- Автоматическое применение результатов LLM отключено.
- Live E2E не проверен.


## 0.4.0rc1

- Added a local operator workflow for normalization REVIEW items.
- Added append-only, idempotent decision logging.
- Added source fingerprints to prevent applying decisions to changed rows.
- Added versioned rule metadata and application audit records.
- Added `review list`, `review decide`, and `review apply` CLI commands.
- Kept NTD/reference research outside this workflow.
- Added 7 regression tests for the review workflow.


## 0.3.2

### Stabilization

- Aligned the root release manifest with v0.3.2 and catalog v1.1.0.
- Moved the obsolete v0.3.0 installer from the active project root to `tools/legacy/`.
- Corrected the pilot metric `ambiguous_fuzzy_tie_queries` from `0` to `1`.
- Reconfirmed the clean regression result: `94 passed, 1 skipped`.

- Round products accept a single diameter; weight is stored as quantity.
- Inch tube sizes remain in inches with a separate reference millimetre value.
- Scrap group is stored separately and displayed as `Лом, гр. Бxx`.
- P32/P48 mesh parameters are derived from ГОСТ 3187-76 without guessing material grade.
- Reference research is separated from parsing review.
- Operator hints support probable normative designations without automatic replacement.
- Equal fuzzy candidates receive `AMBIGUOUS_FUZZY_TIE`.
- No automatic application of fuzzy, equivalence, or near-size candidates.

## 0.3.0 — 2026-06-18 — Nomenclature Search MVP

- Added a separate `price_parser.nomenclature` module without changing the legacy search flow.
- Added exact, alias, confirmed/proposed relation, fuzzy, and near-dimension search levels.
- Added versioned JSONL catalogs with validation, sources, statuses, and JSON Schemas.
- Added inventory JSONL loading and a traceable adapter from `ParsedItem`.
- Added `price-parser search` CLI with JSON output and no external API calls.
- Added explicit `requires_review`, warnings, reasons, and `automatic_application_performed=false`.
- Added rule clarification register, decision matrix, and grouped approval questions.
- Added 20 unit/regression tests for search, catalog safety, and traceability.

## 0.2.6 — 2026-06-18 — АВ.Т1 supply-state separation

- Canonicalizes `АВ.Т1` and `AB.T1` to grade `АВ`.
- Moves `Т1` to additional information as supply condition.
- Removes the obsolete ambiguity warning after local validation.
- Preserves unrelated grades such as `В95Т1`.
- Replays saved results locally without LLM calls.
- Documents installation and rollback from the complete release archive.
- Release revision r2 adds `pydantic` to the test extra so a clean installation can collect the LLM pilot tests.
## 0.2.5 - Deterministic LLM decision reconciliation

- Computes `KEEP`, `PROPOSE_CHANGE`, or `REVIEW` after local normalization.
- Removes false grade-conflict warnings only when grades are equivalent.
- Preserves model `REVIEW` decisions and unrelated warnings.
- Stores model decisions and a reconciliation audit trail.
- Recalculates pilot evaluation locally without additional API calls.
- Adds regression tests and automatic post-processing to `run_llm_pilot.ps1`.

## 0.2.4 - Grade layout canonicalization

- Added deterministic Cyrillic/Latin grade matching.
- Canonicalized `A75` and `А75` to customer-facing `А75`.
- Reused project normalization in search and LLM pilot evaluation.
- Corrected CTRL-001 gold layout.
- Added local regression and replay checks without API calls.

## 0.2.3 — 2026-06-18

- добавлена отдельная команда `llm-pilot`, не изменяющая production XLSX;
- подготовлена выборка из 15 контрольных и 10 неоднозначных строк;
- эталон хранится отдельно и не передаётся модели;
- добавлены лимит строк, dry-run и обязательный флаг `--confirm-live-api`;
- добавлена строгая structured-output схема с решениями KEEP/PROPOSE_CHANGE/REVIEW;
- ответы сохраняются как replay-фикстуры;
- рассчитываются точность, токены, время и оценка стоимости;
- первый пилот не использует web search и не выдаёт модель за источник НТД;
- модель по умолчанию: `gpt-5.4-mini`, но задаётся через `LLM_MODEL`.

## 0.2.2 — 2026-06-17

- добавлена отраслевая маршрутизация LLM и запреты на недоказанные выводы;
- введены внутренние поля `domain`, `requires_review`, `review_reasons`;
- группы лома Б18, Б26, Б28, Б32 и Б55 экспортируются как `гр. Бxx`;
- конкретная марка стали не выводится из группы лома;
- правило размещения группы лома в колонке «Марка» помечено как ожидающее решения заказчика;
- добавлена отдельная обработка шихты и необязательных размеров для лома;
- добавлены подтверждённые обозначения и алиасы текущей выборки;
- исправлены размеры `т.6*30`, `Х23Ю5-М`, `ф70*17,5`, дисков, Ду/Ру, S, кольцевой плиты и `32хНД`;
- устранены ложные конфликты кириллического `С10200` и ложное распознавание размера `1340` как марки АВ;
- полный прогон: 4 371 позиция, 29 кандидатов, 32 предупреждения, 152 результата поиска;
- тесты: 46 passed; отдельный интеграционный прогон трёх исходных файлов пройден.

## 0.2.1 — 2026-06-17

- добавлена проверка конфликтов марки между колонкой поставщика и описанием;
- устранены ложные конфликты, когда исходная марка явно повторяется в описании;
- добавлен разбор `OD/ID` для труб и расчёт толщины стенки;
- добавлена обработка составной записи `08(12)Х18Н10Т`;
- расширен проверяемый реестр марок только подтверждёнными обозначениями;
- добавлены коды причин `unconfirmed_grade`, `grade_conflict`,
  `dimension_unparsed`, `low_validation_score`;
- служебные суффиксы `Cu-ETP` перенесены из марки в комментарий;
- полный интеграционный прогон: 4 371 позиция, 66 LLM-кандидатов;
- добавлены регрессионные тесты, полный набор: 27 passed.

## 0.2.0 — 2026-06-17

- добавлен проверяемый реестр металлургических марок;
- подтверждённые марки распознаются до эвристик;
- `мин.` и другие служебные токены больше не распознаются как марки;
- исправлена строка `Квадрат N060 12х13`: марка 12Х13, размер 60;
- при двух подтверждённых марках основной становится первая по тексту;
- добавлены регрессионные тесты справочника.

## 0.1.0 — 2026-06-17

### Добавлено

- локальный конвейер `.xls/.xlsx`;
- три адаптера поставщиков и generic fallback;
- нормализация номенклатуры;
- поиск БрБ2 по точному и ближайшему размеру;
- XLSX-экспорт;
- статистика и список LLM-кандидатов;
- OpenAI structured-output адаптер;
- replay-провайдер;
- тесты и документация.

### Известные ограничения

- реальный OpenAI API ещё не вызывался;
- generic fallback покрыт unit-тестом, но не прошёл приёмочный прогон на реальном неизвестном файле;
- 171 строка требует AI-проверки.
