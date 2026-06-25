# Backend API — Price Parser 0.5.0rc1

## Запуск

```powershell
price-parser api serve `
  --host 127.0.0.1 `
  --port 8765
```

По умолчанию API доступен только локально. Для внешнего интерфейса обязателен
`API_TOKEN`, передаваемый клиентом в заголовке `X-API-Key`.

## Endpoints

- `GET /health` — версия, БД и статус;
- `GET /stats` — количества записей;
- `POST /imports/pilot` — идемпотентный импорт каталога отчётов;
- `GET /imports/{job_id}` — статус импорта;
- `GET /offers/{offer_id}` — предложение с provenance;
- `GET /search` — объяснимый поиск;
- `GET /reviews` — очередь REVIEW;
- `POST /reviews/{offer_id}/decisions` — решение оператора;
- `POST /reviews/{offer_id}/apply` — явное применение решения;
- `POST /llm-runs` — регистрация LLM-запуска.

## Ограничения

- API пока локальный и синхронный;
- загрузка исходных файлов через HTTP не реализована;
- живой LLM E2E не проверен;
- LLM и поиск ничего не применяют автоматически;
- field IDs amoCRM не определены.

## Вопросы проверки и тестирования

Полный накопительный реестр всего проекта находится в
`docs/VERIFICATION_REGISTER.md`. В нём фиксируются статусы `VERIFIED`,
`PARTIALLY_VERIFIED`, `NOT_VERIFIED`, `BLOCKED`, способ проверки,
фактический результат и путь к подтверждению.

