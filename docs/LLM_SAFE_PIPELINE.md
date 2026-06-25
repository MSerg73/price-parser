# Безопасный LLM-контур — 0.7.1rc3

## Возможности

- провайдеры `mock` и `openai`;
- строгая Pydantic-схема;
- контроль соответствия `source_id`;
- запрет пропущенных, добавленных и дублирующихся строк;
- принудительный `REVIEW` при низкой уверенности, непроверенном основании,
  исследовании НТД или необъяснённом изменении;
- локальная reconciliation;
- audit JSON и fingerprint запроса;
- автоматическое применение отключено.

## Офлайн-проверка

```powershell
python -m price_parser llm-safe `
  --input pilot\pilot_input_v0_2_2.jsonl `
  --output-dir C:\AI Test\reports\llm_safe_mock_v0_4_0rc2 `
  --provider mock
```

## Реальная модель

Реальный вызов разрешён только с `--confirm-live-api` и локально заданными
`LLM_API_KEY` и `LLM_MODEL`.

```powershell
python -m price_parser llm-safe `
  --input pilot\pilot_input_v0_2_2.jsonl `
  --output-dir C:\AI Test\reports\llm_safe_live_v0_4_0rc2 `
  --provider openai `
  --confirm-live-api
```

Live API проверен 19.06.2026. Итоговая revalidation сохранённых live/replay выполняется локально без API.

## Вопросы проверки и тестирования

Полный накопительный реестр всего проекта находится в
`docs/VERIFICATION_REGISTER.md`. В нём фиксируются статусы `VERIFIED`,
`PARTIALLY_VERIFIED`, `NOT_VERIFIED`, `BLOCKED`, способ проверки,
фактический результат и путь к подтверждению.


## Правило применения

Любой LLM-ответ является только предложением для REVIEW. Команда `parse --llm`
не изменяет основной XLSX и сохраняет предложения в отдельный JSONL.

Подробности: [LLM_ACCESS_POLICY.md](LLM_ACCESS_POLICY.md).
