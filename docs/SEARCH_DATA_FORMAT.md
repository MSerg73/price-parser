# Формат данных поиска v0.3.0

## Справочник

Встроенный справочник расположен в:

```text
src/price_parser/data/nomenclature/
├── catalog.json
├── grades.jsonl
├── aliases.jsonl
├── equivalences.jsonl
├── dimension_rules.json
└── schema/
```

### Марка

```json
{
  "id": "grade-0016",
  "canonical": "12Х18Н10Т",
  "status": "CONFIRMED",
  "source_reference": "ГОСТ 5632",
  "standard_system": "ГОСТ"
}
```

### Псевдоним

```json
{
  "id": "alias-0001",
  "alias": "C17200",
  "canonical_grade_id": "grade-0001",
  "status": "CONFIRMED",
  "source_reference": "Тестовое задание заказчика"
}
```

### Связь марок

```json
{
  "id": "eq-example",
  "source_grade_id": "grade-a",
  "target_grade_id": "grade-b",
  "relation_type": "APPROXIMATE",
  "status": "PROPOSED",
  "source_reference": "Требуется утверждённый нормативный источник",
  "bidirectional": true
}
```

Статусы:

- `CONFIRMED` — разрешено использовать согласно типу связи;
- `PROPOSED` — только кандидат с ручной проверкой;
- `REJECTED` — исключается из поиска.

Тип `APPROXIMATE` даже в подтверждённом статусе требует ручной проверки.

### Размерное правило

```json
{
  "id": "rule-pipe-example",
  "profile": "ТРУБА",
  "max_absolute_delta": [0, 0.5, null],
  "status": "PROPOSED",
  "source_reference": "Не утверждено"
}
```

В поставляемом справочнике подтверждённых размерных допусков нет. Поэтому
близкие размеры показываются только как кандидаты.

## Наличие поставщиков

CLI принимает JSONL:

```json
{
  "id": "supplier-row-001",
  "supplier": "Поставщик",
  "profile": "ТРУБА",
  "grade": "12Х18Н10Т",
  "dimensions": ["5", "1.5", null],
  "source_reference": "price.xlsx / Лист1 / строка 15",
  "payload": {
    "availability": "100 кг",
    "price_rub_kg": "1000"
  }
}
```

Обязательные поля: `id`, `supplier`, `profile`, `grade`,
`source_reference`. Идентификатор должен быть уникален.

## Результат

Результат содержит:

- исходный запрос;
- нормализованный профиль и ключ марки;
- версию справочника;
- тип совпадения;
- итоговый и частные scores;
- причины;
- предупреждения;
- источник позиции;
- источник связи;
- признак ручной проверки;
- явный запрет автоматического применения.
