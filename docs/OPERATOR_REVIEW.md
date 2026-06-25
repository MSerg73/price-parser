# Операторский цикл REVIEW

Версия: `0.4.0rc1`.

Контур работает локально с JSONL. Он не меняет исходные прайсы, не вызывает LLM и не затрагивает очередь исследования НТД.

## Файлы

- `normalization_review_queue.jsonl` — входная очередь;
- `operator_review_decisions.jsonl` — append-only журнал решений;
- `normalized_items_reviewed.jsonl` — результат применения;
- `normalization_review_queue_remaining.jsonl` — нерешённые строки;
- `operator_review_audit.jsonl` — журнал применений.

## 1. Посмотреть очередь

```powershell
price-parser review list `
  --queue "C:\AI Test\reports\search_pilot_v0_3_2\normalization_review_queue.jsonl" `
  --decisions "C:\AI Test\reports\operator_review\operator_review_decisions.jsonl" `
  --output "C:\AI Test\reports\operator_review\review_status.json"
```

## 2. Записать решение

Оставить текущую нормализацию:

```powershell
price-parser review decide `
  --queue "C:\AI Test\reports\search_pilot_v0_3_2\normalization_review_queue.jsonl" `
  --decisions "C:\AI Test\reports\operator_review\operator_review_decisions.jsonl" `
  --offer-id "offer-..." `
  --action ACCEPT_AS_IS `
  --operator "Operator" `
  --comment "Профиль подтверждён"
```

Изменить поля:

```powershell
price-parser review decide `
  --queue "C:\AI Test\reports\search_pilot_v0_3_2\normalization_review_queue.jsonl" `
  --decisions "C:\AI Test\reports\operator_review\operator_review_decisions.jsonl" `
  --offer-id "offer-..." `
  --action UPDATE_FIELDS `
  --operator "Operator" `
  --comment "Подтверждена классификация ДИСК" `
  --set "profile=ДИСК" `
  --rule-id "PROFILE-DISK" `
  --rule-version "1.0"
```

Отложить:

```powershell
price-parser review decide ... --action DEFER --comment "Нужно решение заказчика"
```

Повтор одинаковой команды не создаёт дубль.

## 3. Применить подтверждённые решения

```powershell
price-parser review apply `
  --items "C:\AI Test\reports\search_pilot_v0_3_2\normalized_items.jsonl" `
  --decisions "C:\AI Test\reports\operator_review\operator_review_decisions.jsonl" `
  --output "C:\AI Test\reports\operator_review\normalized_items_reviewed.jsonl" `
  --remaining-queue "C:\AI Test\reports\operator_review\normalization_review_queue_remaining.jsonl" `
  --audit "C:\AI Test\reports\operator_review\operator_review_audit.jsonl" `
  --applied-by "Operator"
```

## Безопасность

- применяются только `CONFIRMED` решения;
- решение привязано к fingerprint исходной строки;
- при изменении источника решение пропускается;
- исходный provenance сохраняется;
- `automatic_application_performed` остаётся `false`;
- удаления позиций нет;
- очередь НТД не изменяется.

## Вопросы проверки и тестирования

Полный накопительный реестр всего проекта находится в
`docs/VERIFICATION_REGISTER.md`. В нём фиксируются статусы `VERIFIED`,
`PARTIALLY_VERIFIED`, `NOT_VERIFIED`, `BLOCKED`, способ проверки,
фактический результат и путь к подтверждению.

