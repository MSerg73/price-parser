from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .domain_routing import policy_payload
from .llm import ReplayLLMProvider, candidate_reasons, collect_candidates, enrich_items
from .llm.safe_pipeline import MockPilotProvider, run_safe_pipeline
from .llm.pilot_provider import OpenAIPilotProvider
from .llm.pilot_runner import load_jsonl, build_prompt_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price-parser",
        description="Локальный парсер прайс-листов .xls/.xlsx",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_command = subparsers.add_parser("parse", help="Разобрать прайсы и создать XLSX")
    parse_command.add_argument("files", nargs="+", help="Пути к прайс-листам")
    parse_command.add_argument(
        "--output",
        default="result.xlsx",
        help="Итоговый XLSX (по умолчанию result.xlsx)",
    )
    parse_command.add_argument(
        "--query",
        default="пруток БрБ2 ф20",
        help="Контрольный поисковый запрос",
    )
    parse_command.add_argument(
        "--stats",
        default=None,
        help="Необязательный путь для JSON-статистики",
    )
    parse_command.add_argument(
        "--candidates",
        default=None,
        help="Сохранить спорные строки до LLM в JSONL",
    )
    parse_command.add_argument(
        "--llm",
        choices=("none", "openai", "replay"),
        default="none",
        help="Режим LLM. По умолчанию внешних вызовов нет",
    )
    parse_command.add_argument(
        "--llm-batch-size",
        type=int,
        default=1,
        help="Строк в одном LLM-запросе; проверенное значение 1, допустимо 1–10",
    )
    parse_command.add_argument(
        "--llm-review-output",
        default=None,
        help="JSONL с предложениями LLM; основной XLSX не изменяется",
    )
    parse_command.add_argument(
        "--replay-dir",
        default="fixtures/llm",
        help="Каталог replay-фикстур или записи ответов OpenAI",
    )

    pilot = subparsers.add_parser(
        "llm-pilot",
        help="Контролируемый LLM-пилот без изменения production XLSX",
    )
    pilot.add_argument("--input", required=True, help="JSONL с пилотными строками")
    pilot.add_argument("--gold", required=True, help="JSONL с эталоном control-строк")
    pilot.add_argument(
        "--output-dir",
        required=True,
        help="Каталог результатов, метрик и replay-файлов",
    )
    pilot.add_argument(
        "--provider",
        choices=("openai", "replay"),
        default="openai",
        help="Провайдер пилота",
    )
    pilot.add_argument("--model", default=None, help="Модель или значение LLM_MODEL")
    pilot.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Строк в одном запросе; проверенное значение 1, допустимо 1–10",
    )
    pilot.add_argument(
        "--max-cases",
        type=int,
        default=25,
        help="Жёсткий лимит строк в одном пилоте",
    )
    pilot.add_argument(
        "--replay-dir",
        default=None,
        help="Каталог записи/чтения replay; по умолчанию <output-dir>/replay",
    )
    pilot.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверить выборку и показать число вызовов без API",
    )
    pilot.add_argument(
        "--confirm-live-api",
        action="store_true",
        help="Обязательное явное подтверждение реальных OpenAI-вызовов",
    )



    llm_safe = subparsers.add_parser(
        "llm-safe",
        help="Безопасный LLM-контур с обязательной валидацией и без автоприменения",
    )
    llm_safe.add_argument("--input", required=True, help="JSONL пилотных строк")
    llm_safe.add_argument("--output-dir", required=True, help="Каталог результатов")
    llm_safe.add_argument(
        "--provider",
        choices=("mock", "openai"),
        default="mock",
        help="mock работает полностью офлайн; openai требует явного подтверждения",
    )
    llm_safe.add_argument("--model", default=None, help="Модель или LLM_MODEL")
    llm_safe.add_argument(
        "--minimum-confidence",
        type=float,
        default=0.80,
        help="Ниже порога результат принудительно переводится в REVIEW",
    )
    llm_safe.add_argument(
        "--confirm-live-api",
        action="store_true",
        help="Обязательное подтверждение реального API-вызова",
    )


    assignment = subparsers.add_parser(
        "test-assignment",
        help="Сформировать точный комплект тестового задания и отчёт готовности",
    )
    assignment.add_argument("files", nargs="+", help="Пути к .xls/.xlsx")
    assignment.add_argument(
        "--output",
        default="test_assignment_result.xlsx",
        help="XLSX с ровно десятью колонками задания",
    )
    assignment.add_argument(
        "--report-dir",
        default="reports/test_assignment",
        help="Каталог отчёта, LLM audit и candidate JSONL",
    )
    assignment.add_argument(
        "--query",
        default="пруток БрБ2 ф20",
        help="Контрольный поисковый запрос",
    )
    assignment.add_argument(
        "--llm-provider",
        choices=("none", "mock", "openai", "replay"),
        default="mock",
        help="mock закрывает офлайн-контракт; openai требует явного подтверждения",
    )
    assignment.add_argument(
        "--llm-max-cases",
        type=int,
        default=25,
        help="Максимум строк контролируемой LLM-проверки",
    )
    assignment.add_argument("--model", default=None, help="Модель или LLM_MODEL")
    assignment.add_argument(
        "--replay-dir",
        default=None,
        help="Каталог replay для чтения/записи",
    )
    assignment.add_argument(
        "--confirm-live-api",
        action="store_true",
        help="Обязательное подтверждение реального LLM API",
    )


    run_folder = subparsers.add_parser(
        "run-folder",
        help="Обработать все прайсы из Input, применить профильный поиск и создать один XLSX",
    )
    run_folder.add_argument("--input-dir", required=True, help="Папка со всеми .xls/.xlsx")
    run_folder.add_argument("--output-dir", required=True, help="Папка итоговых XLSX")
    run_folder.add_argument("--report-root", required=True, help="Корневая папка отчётов запусков")
    run_folder.add_argument(
        "--profile-query",
        default="",
        help="Legacy: профиль для отбора; пусто означает все позиции",
    )
    run_folder.add_argument(
        "--query",
        default=None,
        help="Полная поисковая заявка, например: пруток БрБ2 ф20",
    )
    run_folder.add_argument(
        "--llm-provider",
        choices=("auto", "none", "mock", "openai", "replay"),
        default="auto",
        help="auto загружает .env и выбирает OpenAI при наличии ключа",
    )
    run_folder.add_argument(
        "--llm-max-cases",
        type=int,
        default=0,
        help=(
            "Диагностический лимит строк: 0 означает обработать все "
            "спорные строки"
        ),
    )
    run_folder.add_argument(
        "--llm-batch-size",
        type=int,
        default=10,
        help="Строк в одном API-запросе; все кандидаты идут пакетами 1–10",
    )
    run_folder.add_argument("--model", default=None, help="Модель или LLM_MODEL")
    run_folder.add_argument("--replay-dir", default=None)
    run_folder.add_argument(
        "--confirm-live-api",
        action="store_true",
        help="Явное подтверждение реального LLM API",
    )
    run_folder.add_argument(
        "--require-live-llm",
        action="store_true",
        help="Не создавать итоговый XLSX без успешного live LLM-вызова",
    )


    llm_audit = subparsers.add_parser(
        "llm-offline-audit",
        help="Проверить LLM-контракт, guardrails и replay без внешнего API",
    )
    llm_audit.add_argument(
        "--output-dir",
        default="reports/llm_offline_audit",
        help="Каталог JSON/Markdown отчёта",
    )

    search = subparsers.add_parser(
        "search",
        help="Объяснимый поиск по нормализованной номенклатуре",
    )
    search.add_argument("--inventory", required=True, help="JSONL с позициями наличия")
    search.add_argument("--profile", required=True, help="Профиль продукции")
    search.add_argument("--grade", required=True, help="Марка или обозначение")
    search.add_argument(
        "--dimensions",
        default=None,
        help="До трёх размеров, например 5x1,5",
    )
    search.add_argument(
        "--catalog-dir",
        default=None,
        help="Необязательный каталог справочника; по умолчанию встроенный",
    )
    search.add_argument("--limit", type=int, default=20, help="Максимум результатов")
    search.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=0.82,
        help="Порог fuzzy-кандидатов 0–1",
    )
    search.add_argument(
        "--no-fuzzy",
        action="store_true",
        help="Отключить fuzzy-кандидаты",
    )
    search.add_argument(
        "--no-proposed-equivalents",
        action="store_true",
        help="Не показывать неподтверждённые связи марок",
    )
    search.add_argument(
        "--no-unconfigured-near",
        action="store_true",
        help="Не показывать близкие размеры без утверждённого правила",
    )
    search.add_argument(
        "--output",
        default=None,
        help="Необязательный путь для JSON-результата",
    )


    review = subparsers.add_parser(
        "review",
        help="Операторский цикл обработки очереди нормализации REVIEW",
    )
    review_subparsers = review.add_subparsers(dest="review_command", required=True)

    review_list = review_subparsers.add_parser(
        "list",
        help="Показать нерешённые или все строки REVIEW",
    )
    review_list.add_argument("--queue", required=True, help="normalization_review_queue.jsonl")
    review_list.add_argument(
        "--decisions",
        default=None,
        help="Журнал operator_review_decisions.jsonl",
    )
    review_list.add_argument(
        "--include-resolved",
        action="store_true",
        help="Включить уже решённые строки",
    )
    review_list.add_argument("--output", default=None, help="Необязательный JSON-отчёт")

    review_decide = review_subparsers.add_parser(
        "decide",
        help="Записать подтверждённое или отложенное решение оператора",
    )
    review_decide.add_argument("--queue", required=True, help="Очередь REVIEW")
    review_decide.add_argument("--decisions", required=True, help="Журнал решений JSONL")
    review_decide.add_argument("--offer-id", required=True, help="ID позиции")
    review_decide.add_argument(
        "--action",
        required=True,
        choices=("ACCEPT_AS_IS", "UPDATE_FIELDS", "DEFER"),
    )
    review_decide.add_argument("--operator", required=True, help="Имя оператора")
    review_decide.add_argument("--comment", required=True, help="Основание решения")
    review_decide.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Изменение поля; можно указать несколько раз",
    )
    review_decide.add_argument("--rule-id", default=None, help="ID подтверждённого правила")
    review_decide.add_argument(
        "--rule-version",
        default=None,
        help="Версия подтверждённого правила",
    )

    review_apply = review_subparsers.add_parser(
        "apply",
        help="Применить только подтверждённые решения к normalized_items.jsonl",
    )
    review_apply.add_argument("--items", required=True, help="normalized_items.jsonl")
    review_apply.add_argument("--decisions", required=True, help="Журнал решений JSONL")
    review_apply.add_argument("--output", required=True, help="Результат JSONL")
    review_apply.add_argument(
        "--remaining-queue",
        required=True,
        help="Оставшаяся очередь REVIEW JSONL",
    )
    review_apply.add_argument("--audit", required=True, help="Журнал применений JSONL")
    review_apply.add_argument(
        "--applied-by",
        required=True,
        help="Исполнитель запуска применения",
    )


    db = subparsers.add_parser(
        "db",
        help="Постоянное хранилище, миграции и импорт пилотных данных",
    )
    db_subparsers = db.add_subparsers(dest="db_command", required=True)

    db_init = db_subparsers.add_parser("init", help="Применить миграции БД")
    db_init.add_argument("--database-url", default=None)

    db_status = db_subparsers.add_parser("status", help="Показать состояние БД")
    db_status.add_argument("--database-url", default=None)

    db_import = db_subparsers.add_parser(
        "import-pilot",
        help="Идемпотентно импортировать отчёты пилота v0.3.2",
    )
    db_import.add_argument("--report-dir", required=True)
    db_import.add_argument("--database-url", default=None)
    db_import.add_argument("--actor", default="CLI")
    db_import.add_argument("--idempotency-key", default=None)

    api = subparsers.add_parser("api", help="Локальный backend API")
    api_subparsers = api.add_subparsers(dest="api_command", required=True)
    api_serve = api_subparsers.add_parser("serve", help="Запустить API")
    api_serve.add_argument("--database-url", default=None)
    api_serve.add_argument("--host", default="127.0.0.1")
    api_serve.add_argument("--port", type=int, default=8765)
    api_serve.add_argument(
        "--api-token",
        default=None,
        help="Необязательный X-API-Key; можно задать API_TOKEN",
    )


    mail = subparsers.add_parser(
        "mail",
        help="Локальный почтовый контур, replay и безопасная отправка",
    )
    mail_subparsers = mail.add_subparsers(dest="mail_command", required=True)

    mail_create = mail_subparsers.add_parser(
        "request-create",
        help="Создать идемпотентный запрос поставщику без отправки",
    )
    mail_create.add_argument("--database-url", default=None)
    mail_create.add_argument("--request-key", required=True)
    mail_create.add_argument("--to", required=True)
    mail_create.add_argument("--from-email", dest="from_email", default=None)
    mail_create.add_argument("--subject", required=True)
    mail_create.add_argument("--body", required=True)
    mail_create.add_argument("--deal-id", default=None)
    mail_create.add_argument("--actor", default="CLI")

    mail_replay = mail_subparsers.add_parser(
        "replay",
        help="Обработать локальный .eml без подключения к IMAP",
    )
    mail_replay.add_argument("--database-url", default=None)
    mail_replay.add_argument("--eml", required=True)
    mail_replay.add_argument("--storage-root", required=True)
    mail_replay.add_argument("--actor", default="CLI")
    mail_replay.add_argument(
        "--max-attachment-bytes",
        type=int,
        default=25 * 1024 * 1024,
    )

    mail_list = mail_subparsers.add_parser(
        "list",
        help="Показать сохранённые письма",
    )
    mail_list.add_argument("--database-url", default=None)
    mail_list.add_argument("--status", default=None)
    mail_list.add_argument("--request-id", default=None)
    mail_list.add_argument("--limit", type=int, default=100)

    mail_send = mail_subparsers.add_parser(
        "send",
        help="Отправить запрос через SMTP только с явным подтверждением",
    )
    mail_send.add_argument("--database-url", default=None)
    mail_send.add_argument("--request-id", required=True)
    mail_send.add_argument("--storage-root", required=True)
    mail_send.add_argument("--smtp-host", default=None)
    mail_send.add_argument("--smtp-port", type=int, default=None)
    mail_send.add_argument("--actor", default="CLI")
    mail_send.add_argument("--confirm-live-mail", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "parse":
        return _run_parse(args)
    if args.command == "llm-pilot":
        return _run_llm_pilot(args)
    if args.command == "llm-safe":
        return _run_llm_safe(args)
    if args.command == "test-assignment":
        return _run_test_assignment(args)
    if args.command == "run-folder":
        return _run_folder(args)
    if args.command == "llm-offline-audit":
        return _run_llm_offline_audit(args)
    if args.command == "search":
        return _run_nomenclature_search(args)
    if args.command == "review":
        return _run_operator_review(args)
    if args.command == "db":
        return _run_database(args)
    if args.command == "api":
        return _run_api(args)
    if args.command == "mail":
        return _run_mail(args)
    return 2



def _run_llm_safe(args: argparse.Namespace) -> int:
    try:
        cases = load_jsonl(args.input)
        payload = build_prompt_payload(cases)
        if args.provider == "openai":
            if not args.confirm_live_api:
                raise ValueError(
                    "Для реального API обязателен флаг --confirm-live-api"
                )
            provider = OpenAIPilotProvider(model=args.model)
        else:
            provider = MockPilotProvider()

        result = run_safe_pipeline(
            payload=payload,
            provider=provider,
            output_dir=args.output_dir,
            minimum_confidence=args.minimum_confidence,
        )
        print(
            json.dumps(
                {
                    "rows": len(result.rows),
                    "model": result.model,
                    "automatic_application_performed": False,
                    "live_model_verified": False,
                    "output_dir": str(Path(args.output_dir).resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(f"Ошибка безопасного LLM-контура: {exc}", file=sys.stderr)
        return 1



def _run_test_assignment(args: argparse.Namespace) -> int:
    from .assignment import run_test_assignment

    try:
        report = run_test_assignment(
            files=args.files,
            output=args.output,
            report_dir=args.report_dir,
            query=args.query,
            llm_provider=args.llm_provider,
            llm_max_cases=args.llm_max_cases,
            llm_model=args.model,
            replay_dir=args.replay_dir,
            confirm_live_api=args.confirm_live_api,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] != "FAILED" else 1
    except Exception as exc:
        print(f"Ошибка тестового задания: {exc}", file=sys.stderr)
        return 1



def _run_folder(args: argparse.Namespace) -> int:
    from .interactive_run import run_input_folder

    try:
        report = run_input_folder(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            report_root=args.report_root,
            profile_query=args.profile_query,
            query=args.query,
            llm_provider=args.llm_provider,
            llm_max_cases=args.llm_max_cases,
            llm_batch_size=args.llm_batch_size,
            llm_model=args.model,
            replay_dir=args.replay_dir,
            confirm_live_api=args.confirm_live_api,
            require_live_llm=args.require_live_llm,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] != "FAILED_LLM_REQUIRED" else 1
    except Exception as exc:
        print(f"Ошибка обработки папки Input: {exc}", file=sys.stderr)
        return 1


def _run_llm_offline_audit(args: argparse.Namespace) -> int:
    from .llm.offline_audit import run_offline_audit

    try:
        report = run_offline_audit(args.output_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "VERIFIED" else 1
    except Exception as exc:
        print(f"Ошибка офлайн-аудита LLM: {exc}", file=sys.stderr)
        return 1


def _run_parse(args: argparse.Namespace) -> int:
    from .parser import parse_files
    from .query_normalization import normalize_search_query
    from .search import search_items
    from .xlsx_exporter import export_xlsx
    try:
        items, stats = parse_files(args.files)
        candidates_before = collect_candidates(items)
        if args.candidates:
            _write_candidates(Path(args.candidates), candidates_before)

        llm_proposals = []
        llm_review_file = None
        if args.llm == "openai":
            from .llm.openai_provider import OpenAIProvider

            provider = OpenAIProvider(replay_dir=args.replay_dir)
            llm_proposals = enrich_items(
                items,
                provider,
                stats,
                batch_size=args.llm_batch_size,
                apply_changes=False,
            )
        elif args.llm == "replay":
            provider = ReplayLLMProvider(args.replay_dir)
            llm_proposals = enrich_items(
                items,
                provider,
                stats,
                batch_size=args.llm_batch_size,
                apply_changes=False,
            )

        if llm_proposals:
            llm_review_file = Path(
                args.llm_review_output
                or f"{Path(args.output).with_suffix('')}.llm_review.jsonl"
            )
            llm_review_file.parent.mkdir(parents=True, exist_ok=True)
            with llm_review_file.open("w", encoding="utf-8") as stream:
                for proposal in llm_proposals:
                    stream.write(json.dumps(proposal, ensure_ascii=False) + "\n")

        search_results = search_items(items, normalize_search_query(args.query))
        export_xlsx(args.output, items, search_results)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    summary = {
        "input_files": stats.input_files,
        "raw_items": stats.raw_items,
        "parsed_items": stats.parsed_items,
        "warnings_before_llm": stats.warnings,
        "llm_candidates": len(candidates_before),
        "llm_calls": stats.llm_calls,
        "llm_input_tokens": stats.llm_input_tokens,
        "llm_output_tokens": stats.llm_output_tokens,
        "llm_proposals": len(llm_proposals),
        "llm_review_file": str(llm_review_file.resolve()) if llm_review_file else None,
        "automatic_application_performed": False,
        "search_results": len(search_results),
        "output": str(Path(args.output).resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.stats:
        Path(args.stats).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


def _run_llm_pilot(args: argparse.Namespace) -> int:
    from .llm.pilot_runner import (
        dry_run_summary,
        load_jsonl,
        run_pilot,
    )

    try:
        cases = load_jsonl(args.input)
        gold = load_jsonl(args.gold)
        if args.dry_run:
            summary = dry_run_summary(
                cases,
                batch_size=args.batch_size,
                max_cases=args.max_cases,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        output_dir = Path(args.output_dir)
        replay_dir = (
            Path(args.replay_dir)
            if args.replay_dir
            else output_dir / "replay"
        )
        if args.provider == "openai":
            if not args.confirm_live_api:
                raise RuntimeError(
                    "Реальные API-вызовы заблокированы. "
                    "Добавьте --confirm-live-api после dry-run."
                )
            from .llm.pilot_provider import OpenAIPilotProvider

            provider = OpenAIPilotProvider(
                model=args.model,
                replay_dir=replay_dir,
            )
        else:
            from .llm.pilot_provider import ReplayPilotProvider

            provider = ReplayPilotProvider(replay_dir)

        metrics = run_pilot(
            cases=cases,
            provider=provider,
            output_dir=output_dir,
            gold=gold,
            batch_size=args.batch_size,
            max_cases=args.max_cases,
        )
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def _run_nomenclature_search(args: argparse.Namespace) -> int:
    from .nomenclature import (
        NomenclatureSearchService,
        SearchOptions,
        SearchQuery,
        load_catalog,
        load_inventory,
        parse_dimensions,
    )

    try:
        catalog = load_catalog(args.catalog_dir)
        inventory = load_inventory(args.inventory)
        service = NomenclatureSearchService(catalog)
        dimension_text = args.dimensions or ""
        dimension_units = (
            ("INCH", None, None)
            if any(token in dimension_text.lower() for token in ('"', "дюйм", "in"))
            or "/" in dimension_text
            else (None, None, None)
        )
        response = service.search(
            SearchQuery(
                profile=args.profile,
                grade=args.grade,
                dimensions=parse_dimensions(args.dimensions),
                source_reference="CLI",
                dimension_units=dimension_units,
            ),
            inventory,
            SearchOptions(
                limit=args.limit,
                fuzzy_threshold=args.fuzzy_threshold,
                include_fuzzy=not args.no_fuzzy,
                include_proposed_equivalents=not args.no_proposed_equivalents,
                include_unconfigured_near_dimensions=not args.no_unconfigured_near,
            ),
        )
        payload = response.to_dict()
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Ошибка поиска: {exc}", file=sys.stderr)
        return 1
    return 0



def _run_operator_review(args: argparse.Namespace) -> int:
    from .operator_review import (
        apply_confirmed_decisions,
        list_review_items,
        parse_change_assignments,
        record_decision,
    )

    try:
        if args.review_command == "list":
            payload = list_review_items(
                args.queue,
                args.decisions,
                include_resolved=args.include_resolved,
            )
        elif args.review_command == "decide":
            decision, created = record_decision(
                queue_path=args.queue,
                decisions_path=args.decisions,
                offer_id=args.offer_id,
                action=args.action,
                operator=args.operator,
                comment=args.comment,
                changes=parse_change_assignments(args.set),
                rule_id=args.rule_id,
                rule_version=args.rule_version,
            )
            payload = {
                "created": created,
                "decision": decision,
            }
        elif args.review_command == "apply":
            payload = apply_confirmed_decisions(
                items_path=args.items,
                decisions_path=args.decisions,
                output_path=args.output,
                remaining_queue_path=args.remaining_queue,
                audit_path=args.audit,
                applied_by=args.applied_by,
            )
        else:
            return 2

        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        print(rendered)
        output = getattr(args, "output", None)
        if args.review_command == "list" and output:
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"Ошибка REVIEW: {exc}", file=sys.stderr)
        return 1


def _write_candidates(path: Path, candidates) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in candidates:
            stream.write(
                json.dumps(
                    {
                        "source_id": item.source.display(),
                        "supplier": item.supplier,
                        "description": item.raw_description,
                        "profile": item.profile,
                        "grade": item.grade,
                        "dim1": None if item.dim1 is None else format(item.dim1, "f"),
                        "dim2": None if item.dim2 is None else format(item.dim2, "f"),
                        "dim3": None if item.dim3 is None else format(item.dim3, "f"),
                        "domain": item.domain,
                        "domain_policy": policy_payload(item.domain),
                        "requires_review": item.requires_review,
                        "reason_codes": candidate_reasons(item),
                        "warnings": item.warnings,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _run_database(args: argparse.Namespace) -> int:
    from .db.commands import database_status, upgrade_database
    from .db.services import (
        import_job_to_dict,
        import_pilot_report,
        persistence_counts,
    )
    from .db.session import create_engine_and_session

    try:
        if args.db_command == "init":
            url = upgrade_database(args.database_url)
            payload = {
                "database_url": url,
                "status": database_status(url),
            }
        elif args.db_command == "status":
            payload = database_status(args.database_url)
        elif args.db_command == "import-pilot":
            url = upgrade_database(args.database_url)
            engine, factory = create_engine_and_session(url)
            try:
                with factory() as session:
                    job, created = import_pilot_report(
                        session,
                        args.report_dir,
                        actor=args.actor,
                        idempotency_key=args.idempotency_key,
                    )
                    payload = {
                        "created": created,
                        "job": import_job_to_dict(job),
                        "counts": persistence_counts(session),
                    }
            finally:
                engine.dispose()
        else:
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Ошибка БД: {exc}", file=sys.stderr)
        return 1



def _run_mail(args: argparse.Namespace) -> int:
    import os

    from .db.commands import upgrade_database
    from .db.session import create_engine_and_session
    from .mail import (
        SMTPConfig,
        SMTPTransport,
        create_mail_request,
        list_mail_messages,
        replay_eml,
        send_mail_request,
    )
    from .mail.service import mail_message_to_dict, mail_request_to_dict

    try:
        url = upgrade_database(args.database_url)
        engine, factory = create_engine_and_session(url)
        try:
            with factory() as session:
                if args.mail_command == "request-create":
                    item, created = create_mail_request(
                        session,
                        request_key=args.request_key,
                        recipient_email=args.to,
                        sender_email=args.from_email,
                        subject=args.subject,
                        body_text=args.body,
                        deal_external_id=args.deal_id,
                        actor=args.actor,
                    )
                    payload = {
                        "created": created,
                        "request": mail_request_to_dict(item),
                    }
                elif args.mail_command == "replay":
                    item, created = replay_eml(
                        session,
                        args.eml,
                        storage_root=args.storage_root,
                        actor=args.actor,
                        max_attachment_bytes=args.max_attachment_bytes,
                    )
                    payload = {
                        "created": created,
                        "message": mail_message_to_dict(item),
                    }
                elif args.mail_command == "list":
                    payload = list_mail_messages(
                        session,
                        status=args.status,
                        request_id=args.request_id,
                        limit=args.limit,
                    )
                elif args.mail_command == "send":
                    if not args.confirm_live_mail:
                        raise RuntimeError(
                            "Для SMTP обязателен --confirm-live-mail"
                        )
                    username = (os.getenv("MAIL_USERNAME") or "").strip()
                    password = os.getenv("MAIL_PASSWORD") or ""
                    host = (args.smtp_host or os.getenv("MAIL_SMTP_HOST") or "").strip()
                    port = args.smtp_port or int(os.getenv("MAIL_SMTP_PORT") or "465")
                    if not username or not password or not host:
                        raise RuntimeError(
                            "Нужны MAIL_USERNAME, MAIL_PASSWORD и MAIL_SMTP_HOST"
                        )
                    transport = SMTPTransport(
                        SMTPConfig(
                            host=host,
                            port=port,
                            username=username,
                            password=password,
                            use_ssl=True,
                        )
                    )
                    item, changed = send_mail_request(
                        session,
                        request_id=args.request_id,
                        transport=transport,
                        storage_root=args.storage_root,
                        actor=args.actor,
                        confirm_live_mail=True,
                    )
                    payload = {
                        "changed": changed,
                        "request": mail_request_to_dict(item),
                    }
                else:
                    return 2
        finally:
            engine.dispose()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Ошибка почтового контура: {exc}", file=sys.stderr)
        return 1


def _run_api(args: argparse.Namespace) -> int:
    import os

    if args.api_command != "serve":
        return 2
    try:
        token = (args.api_token or os.getenv("API_TOKEN") or "").strip() or None
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
            raise RuntimeError(
                "Запуск API на внешнем интерфейсе без API_TOKEN запрещён"
            )
        from .api import create_app
        import uvicorn

        app = create_app(
            args.database_url,
            auto_migrate=True,
            api_token=token,
        )
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    except Exception as exc:
        print(f"Ошибка API: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
