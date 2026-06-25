# Database schema — Price Parser 0.5.0rc1

## Назначение

Постоянное хранение исходных документов, строк, предложений, REVIEW, задач НТД,
LLM-запусков и аудита.

## Сущности

- `suppliers` — поставщики;
- `source_documents` — исходные файлы с SHA-256;
- `source_rows` — provenance: файл, лист, строка, блок и исходный текст;
- `import_jobs` — идемпотентные импорты;
- `offers` — нормализованные предложения;
- `review_items` и `review_decisions` — операторский цикл;
- `reference_research_tasks` — отдельный контур исследования НТД;
- `llm_runs` — журнал LLM-запусков без автоприменения;
- `audit_events` — важные действия.

## База данных

Локально используется SQLite:

```text
sqlite:///C:/AI Test/data/price_parser.db
```

Модели и миграции совместимы с PostgreSQL. Для PostgreSQL устанавливается extra:

```powershell
pip install -e ".[backend,postgres]"
```

## Миграции

```powershell
price-parser db init
price-parser db status
```

Первая миграция: `0001`.

## Вопросы проверки и тестирования

Полный накопительный реестр всего проекта находится в
`docs/VERIFICATION_REGISTER.md`. В нём фиксируются статусы `VERIFIED`,
`PARTIALLY_VERIFIED`, `NOT_VERIFIED`, `BLOCKED`, способ проверки,
фактический результат и путь к подтверждению.

