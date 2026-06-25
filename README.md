# Industrial Price Parser 0.7.1rc14

Публичная обезличенная версия инструмента для разбора промышленных прайс-листов `.xls/.xlsx`, нормализации номенклатуры, поиска товаров и контролируемого применения результатов LLM.

Текущий релиз: **`v0.7.1-rc14`** — pre-release.

## Возможности

- чтение прайс-листов `.xls` и `.xlsx`;
- специализированные и универсальный парсеры поставщиков;
- извлечение профиля, марки, размеров, наличия и цены;
- нормализация кириллицы/латиницы, разделителей `x / х / × / *`, запятых и точек;
- поиск по профилю, марке и размерам;
- ранжирование точных и ближайших совпадений;
- очередь ручной проверки для неоднозначных строк;
- безопасный LLM-контур со Structured Output, `evidence` и программной валидацией;
- экспорт итогового XLSX из десяти колонок;
- отчёты, replay и аудит изменений;
- локальные тесты без обязательного внешнего API.

## Что изменено в RC14

RC14 устраняет разрыв между LLM-анализом и итоговым результатом.

Конвейер `run-folder` работает так:

```text
чтение прайсов
→ локальный разбор
→ выделение спорных строк
→ LLM-анализ
→ проверка evidence
→ reconciliation
→ применение подтверждённых изменений
→ поиск
→ итоговый XLSX
→ аудит
```

Основные изменения:

- профиль восстанавливается из явного слова в исходном описании, если отдельная колонка профиля пуста;
- материал, марка, профиль и размеры рассматриваются как разные сущности;
- полный снимок исходной строки и распознанных заголовков сохраняется для аудита;
- для каждого предлагаемого изменения LLM должна вернуть причину и точный `evidence`;
- применяются только решения `PROPOSE_CHANGE`, подтверждённые исходным текстом или исходной колонкой;
- решения `KEEP` и `REVIEW` не изменяют итоговую позицию;
- поиск и XLSX формируются после reconciliation;
- создаются `source_audit.jsonl` и `llm_application_audit.json`;
- генерация переводов, химических символов и синонимов материалов отключена;
- формат итогового XLSX не изменён.

Пример:

```text
Титан уголок | профиль пуст | 4х4 | 2740мм | 11.5 кг
```

Результат:

```text
Профиль:  УГОЛОК
Материал: ТИТАН
Марка:    не указана
Размер 1: 4
Размер 2: 4
Размер 3: 2740
Наличие:  11.5 кг
```

Материал хранится во внутренней модели, поисковом тексте, комментарии и audit-файле. В десятиколоночной выходной схеме отдельной колонки «Материал» нет.

## Правила применения LLM

LLM не является источником истины. Модель предлагает решение, а программа проверяет его по исходным данным.

| Решение | Поведение |
|---|---|
| `KEEP` | локальный результат сохраняется |
| `PROPOSE_CHANGE` с подтверждённым evidence | изменение применяется |
| `PROPOSE_CHANGE` без подтверждения | строка переводится в REVIEW |
| `REVIEW` | автоматическое изменение запрещено |
| конфликт значений | требуется ручная проверка |

Важно различать режимы:

- `run-folder` в RC14 может применять только проверенные изменения до поиска и XLSX;
- `llm-safe` и `llm-pilot` остаются изолированными режимами проверки и сами production-результат не изменяют;
- replay позволяет повторно проверить сохранённый ответ без нового API-вызова.

## Выходная схема XLSX

Итоговый файл содержит один лист и десять колонок:

1. Поставщик
2. Профиль
3. Марка
4. Размер 1
5. Размер 2
6. Размер 3
7. Наличие
8. Цена (₽/кг)
9. Комментарий
10. Источник (файл/строка)

Исходный файл, лист и строка сохраняются для трассировки результата.

## Установка

Требуется Python **3.11+**.

### Windows PowerShell

```powershell
git clone https://github.com/MSerg73/price-parser.git
Set-Location price-parser

python -m venv .venv
. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[test,llm]"
```

Проверка установки:

```powershell
price-parser --help
python -m pytest -q
```

### Linux/macOS

```bash
git clone https://github.com/MSerg73/price-parser.git
cd price-parser

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[test,llm]"

price-parser --help
python -m pytest -q
```

## Быстрый запуск без внешнего API

Создайте отдельные каталоги для входных файлов, результатов и отчётов. Реальные прайсы не должны добавляться в Git.

```powershell
price-parser run-folder `
  --input-dir "C:\PriceParser\input" `
  --output-dir "C:\PriceParser\output" `
  --report-root "C:\PriceParser\reports" `
  --query "пруток БрБ2 ф20" `
  --llm-provider none
```

Такой запуск выполняет локальный разбор, поиск, экспорт и отчёт без обращения к LLM API.

## Запуск с LLM

Скопируйте шаблон:

```powershell
Copy-Item .env.example .env
```

Заполните локальный `.env`:

```env
OPENAI_API_KEY=
LLM_MODEL=
```

`LLM_API_KEY` поддерживается только для совместимости. Если одновременно заданы два разных ключа, выполнение должно быть остановлено.

Запуск:

```powershell
price-parser run-folder `
  --input-dir "C:\PriceParser\input" `
  --output-dir "C:\PriceParser\output" `
  --report-root "C:\PriceParser\reports" `
  --query "пруток БрБ2 ф20" `
  --llm-provider auto `
  --llm-batch-size 10 `
  --llm-max-cases 0 `
  --confirm-live-api
```

Для обязательного live-вызова добавьте:

```text
--require-live-llm
```

При этом итоговый XLSX не должен считаться успешным, если обязательный LLM-вызов не выполнен.

## Основные команды

### Разобрать один или несколько файлов

```powershell
price-parser parse `
  "C:\PriceParser\input\price_1.xlsx" `
  "C:\PriceParser\input\price_2.xls" `
  --output "C:\PriceParser\output\result.xlsx" `
  --stats "C:\PriceParser\reports\run_stats.json" `
  --llm none
```

### Сформировать комплект тестового задания

```powershell
price-parser test-assignment `
  "C:\PriceParser\input\price_1.xlsx" `
  --output "C:\PriceParser\output\test_assignment_result.xlsx" `
  --report-dir "C:\PriceParser\reports\test_assignment" `
  --llm-provider mock
```

### Безопасный офлайн-аудит LLM

```powershell
price-parser llm-offline-audit `
  --output-dir "C:\PriceParser\reports\llm_offline_audit"
```

### Работа с очередью REVIEW

```powershell
price-parser review list `
  --queue normalization_review_queue.jsonl
```

Полный список параметров:

```powershell
price-parser --help
price-parser run-folder --help
price-parser review --help
```

## Отчёты и аудит

Для `run-folder` создаётся каталог отдельного запуска. В зависимости от режима он может содержать:

```text
processing_report.txt
processing_report.json
source_audit.jsonl
llm_application_audit.json
llm_batches.json
llm_safe/
replay/
```

`llm_application_audit.json` хранит проверяемую последовательность:

```text
before
→ proposal
→ validation
→ after
```

Неподтверждённое предложение не должно изменять итоговые данные.

## Структура репозитория

```text
src/price_parser/        основной Python-пакет
tests/                   unit и regression tests
docs/                    техническая документация
examples/                обезличенные примеры JSONL
fixtures/llm/            каталог безопасных replay-фикстур
pilot/                   минимальный синтетический fixture
tools/                   локальные smoke и сервисные проверки
.env.example             шаблон переменных окружения
pyproject.toml           зависимости и entry point
CHANGELOG.md             история версий
PUBLIC_REPOSITORY.md     состав публичной публикации
```

## Безопасность

В публичный репозиторий намеренно не включены:

- реальные прайсы поставщиков;
- клиентские данные;
- `.env`;
- API-ключи и пароли;
- live LLM-ответы;
- локальные отчёты и replay с рабочими данными;
- базы данных и production-дампы.

Запрещается добавлять секреты в код, README, Issues, Pull Requests или frontend.

Используйте переменные окружения и локальный `.env`, который исключён через `.gitignore`.

## Тестирование

Полный локальный прогон:

```powershell
python -m pytest -q
```

Проверка компиляции:

```powershell
python -m compileall -q src tests
```

Реальные прайсы для интеграционного прогона должны храниться вне репозитория.

Тест считается подтверждённым только после фактического запуска. Наличие тестового файла само по себе не означает успешное прохождение.

## Известные ограничения RC14

- Excel cell comments пока не читаются;
- семантика объединённых ячеек поддерживается ограниченно;
- неизвестная структура прайса может потребовать REVIEW или новый адаптер;
- формат итогового XLSX ограничен десятью колонками;
- материал не имеет отдельной колонки в итоговом XLSX;
- live LLM зависит от доступности провайдера, модели, ключа и лимитов;
- публичный репозиторий не содержит реальных прайсов для воспроизведения закрытого интеграционного прогона;
- GUI отсутствует, основной интерфейс — CLI.

## Версия и релиз

- ветка: `main`;
- тег: `v0.7.1-rc14`;
- статус: pre-release;
- изменения версий: [CHANGELOG.md](CHANGELOG.md);
- релиз: [Industrial Price Parser 0.7.1rc14](https://github.com/MSerg73/price-parser/releases/tag/v0.7.1-rc14).

## Статус проекта

RC14 является тестовой релизной версией Price Parser. Публичная публикация предназначена для просмотра архитектуры, кода, тестов и документации.

Фактическая проверка на новых прайсах, live API и рабочих данных выполняется отдельно в локальном окружении.
