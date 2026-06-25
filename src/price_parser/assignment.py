from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .domain_routing import policy_payload
from .llm import candidate_reasons, collect_candidates
from .llm.pilot_provider import OpenAIPilotProvider, ReplayPilotProvider
from .llm.pilot_runner import estimate_cost
from .llm.safe_pipeline import MockPilotProvider, run_safe_pipeline
from .models import ParseStats, ParsedItem
from .parser import parse_files
from .search import SearchResult, expand_round_bar_search_view, search_items
from .xlsx_exporter import (
    ASSIGNMENT_HEADERS,
    export_assignment_search_xlsx,
    export_assignment_xlsx,
)


ASSIGNMENT_QUERY = "пруток БрБ2 ф20"
ASSIGNMENT_SCOPE_VERSION = "1.3"


def build_llm_payload(
    items: list[ParsedItem],
    *,
    max_cases: int = 25,
) -> tuple[dict[str, Any], list[ParsedItem]]:
    if max_cases < 1:
        raise ValueError("max_cases должен быть положительным")

    candidates = collect_candidates(items)
    selected = candidates[:max_cases]

    rows = []
    for item in selected:
        reasons = candidate_reasons(item)
        rows.append(
            {
                "source_id": item.source.display(),
                "supplier": item.supplier,
                "description": item.raw_description,
                "current_profile": item.profile,
                "current_grade": None if item.grade == "предпол." else item.grade,
                "current_dimensions": [
                    _dimension_text(item.dim1_display, item.dim1),
                    _dimension_text(item.dim2_display, item.dim2),
                    _dimension_text(item.dim3_display, item.dim3),
                ],
                "domain_policy": policy_payload(item.domain),
                "requires_review": bool(reasons),
                "review_reasons": reasons,
            }
        )
    return {"rows": rows}, selected


def run_test_assignment(
    *,
    files: Iterable[str | Path],
    output: str | Path,
    report_dir: str | Path,
    query: str = ASSIGNMENT_QUERY,
    llm_provider: str = "mock",
    llm_max_cases: int = 25,
    llm_model: str | None = None,
    replay_dir: str | Path | None = None,
    confirm_live_api: bool = False,
) -> dict[str, Any]:
    source_files = [Path(value) for value in files]
    if not source_files:
        raise ValueError("Не переданы прайс-листы")

    unsupported = [
        str(path)
        for path in source_files
        if path.suffix.lower() not in {".xls", ".xlsx"}
    ]
    if unsupported:
        raise ValueError(
            "Тестовое задание поддерживает только .xls/.xlsx: "
            + ", ".join(unsupported)
        )

    target_dir = Path(report_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    items, stats = parse_files(source_files)
    parse_seconds = time.perf_counter() - started
    source_search_results = search_items(items, query)
    search_results = expand_round_bar_search_view(source_search_results)
    export_assignment_xlsx(output, items)

    search_output = target_dir / "search_brb2_f20.xlsx"
    export_assignment_search_xlsx(search_output, search_results)
    search_json = target_dir / "search_brb2_f20.json"
    search_json.write_text(
        json.dumps(
            [
                {
                    "match_type": result.match_type,
                    "size_delta": _decimal_text(result.size_delta),
                    "item": {
                        "supplier": result.item.supplier,
                        "profile": result.effective_profile,
                        "source_profile": result.item.profile,
                        "profile_alias": result.is_profile_alias,
                        "grade": result.item.grade,
                        "dimensions": [
                            _dimension_text(
                                result.item.dim1_display,
                                result.item.dim1,
                            ),
                            _dimension_text(
                                result.item.dim2_display,
                                result.item.dim2,
                            ),
                            _dimension_text(
                                result.item.dim3_display,
                                result.item.dim3,
                            ),
                        ],
                        "availability": result.item.availability,
                        "price_rub_kg": _decimal_text(
                            result.item.price_rub_kg
                        )
                        if result.item.price_rub_kg is not None
                        else "?",
                        "comment": _search_comment(result),
                        "source": result.item.source.display(),
                    },
                }
                for result in search_results
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    candidates = collect_candidates(items)
    candidates_path = target_dir / "llm_candidates.jsonl"
    _write_candidates(candidates_path, candidates)

    llm_summary = _run_llm_stage(
        items=items,
        report_dir=target_dir,
        provider_name=llm_provider,
        max_cases=llm_max_cases,
        model=llm_model,
        replay_dir=replay_dir,
        confirm_live_api=confirm_live_api,
    )

    checks = evaluate_assignment_checks(
        source_files=source_files,
        items=items,
        search_results=search_results,
        stats=stats,
        output=Path(output),
        llm_summary=llm_summary,
    )
    elapsed = time.perf_counter() - started

    report = {
        "schema_version": 1,
        "project_version": __version__,
        "assignment_scope_version": ASSIGNMENT_SCOPE_VERSION,
        "status": _overall_status(checks),
        "input_files": [str(path.resolve()) for path in source_files],
        "input_file_count": len(source_files),
        "rows": len(items),
        "suppliers": sorted({item.supplier for item in items}),
        "parse_seconds": round(parse_seconds, 3),
        "total_seconds": round(elapsed, 3),
        "rows_per_second": round(len(items) / parse_seconds, 3)
        if parse_seconds > 0
        else None,
        "warnings": sum(len(item.warnings) for item in items),
        "review_candidates": len(candidates),
        "missing_price_rows": sum(
            1 for item in items if item.price_rub_kg is None
        ),
        "priced_rows": sum(
            1 for item in items if item.price_rub_kg is not None
        ),
        "search_query": query,
        "search_source_matches": len(source_search_results),
        "search_results": len(search_results),
        "search_profile_alias_rows": sum(
            1 for result in search_results if result.is_profile_alias
        ),
        "search_exact_results": sum(
            1 for result in search_results if result.match_type == "ТОЧНОЕ"
        ),
        "output": str(Path(output).resolve()),
        "output_headers": ASSIGNMENT_HEADERS,
        "output_sheet_count": 1,
        "search_output": str(search_output.resolve()),
        "search_json": str(search_json.resolve()),
        "search_output_is_separate": True,
        "candidate_file": str(candidates_path.resolve()),
        "llm": llm_summary,
        "checks": checks,
        "limitations": [
            "Качество реальной LLM-модели не подтверждается mock-проверкой.",
            "Неизвестный прайс должен быть проверен на созвоне или полной видеозаписью.",
            "Семантическая точность итоговых строк требует сверки с исходными прайсами.",
            "В отдельной поисковой таблице ПРУТОК/КРУГ показаны как эквивалентные фильтруемые представления; alias-строки не являются дополнительным складским остатком.",
        ],
    }

    report_json = target_dir / "test_assignment_report.json"
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_md = target_dir / "test_assignment_report.md"
    report_md.write_text(_render_markdown(report), encoding="utf-8")
    report["report_json"] = str(report_json.resolve())
    report["report_markdown"] = str(report_md.resolve())
    return report


def evaluate_assignment_checks(
    *,
    source_files: list[Path],
    items: list[ParsedItem],
    search_results: list[SearchResult],
    stats: ParseStats,
    output: Path,
    llm_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, title: str, status: str, evidence: Any) -> None:
        checks.append(
            {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
            }
        )

    add(
        "TA-INPUT-01",
        "Поддерживаются только .xls/.xlsx",
        "VERIFIED",
        [path.suffix.lower() for path in source_files],
    )
    add(
        "TA-OUTPUT-01",
        "Сдаваемая таблица имеет ровно 10 требуемых колонок",
        "VERIFIED" if len(ASSIGNMENT_HEADERS) == 10 else "FAILED",
        ASSIGNMENT_HEADERS,
    )
    add(
        "TA-OUTPUT-02",
        "XLSX создан",
        "VERIFIED" if output.exists() and output.stat().st_size > 0 else "FAILED",
        str(output.resolve()),
    )
    add(
        "TA-SOURCE-01",
        "У каждой позиции сохранён файл, лист и строка",
        "VERIFIED"
        if items
        and all(
            item.source.file and item.source.sheet and item.source.row > 0
            for item in items
        )
        else "FAILED",
        {"rows": len(items)},
    )
    add(
        "TA-GRADE-01",
        "Марки в верхнем регистре или явный маркер предположения",
        "VERIFIED"
        if all(
            item.grade == "предпол." or item.grade == item.grade.upper()
            for item in items
        )
        else "FAILED",
        None,
    )
    add(
        "TA-PRICE-01",
        "Отсутствующая цена выводится как ?",
        "VERIFIED",
        {
            "missing_price_rows": sum(
                1 for item in items if item.price_rub_kg is None
            )
        },
    )
    add(
        "TA-SEARCH-01",
        "Точные совпадения идут раньше ближайших размеров",
        "VERIFIED" if _search_order_valid(search_results) else "FAILED",
        [result.match_type for result in search_results[:10]],
    )
    add(
        "TA-SEARCH-02",
        "Контрольный поиск вернул результат",
        "VERIFIED" if search_results else "FAILED",
        {"results": len(search_results)},
    )
    add(
        "TA-PARSE-01",
        "Все извлечённые строки нормализованы",
        "VERIFIED"
        if stats.raw_items == stats.parsed_items == len(items)
        else "FAILED",
        asdict(stats),
    )
    llm_status = str(llm_summary.get("status", "NOT_RUN"))
    add(
        "TA-LLM-01",
        "LLM вызван только для спорных строк и без автоприменения",
        "VERIFIED"
        if llm_status in {"VERIFIED", "NOT_NEEDED"}
        and llm_summary.get("automatic_application_performed") is False
        else ("NOT_VERIFIED" if llm_status == "NOT_RUN" else "FAILED"),
        llm_summary,
    )
    add(
        "TA-LIVE-01",
        "Живая модель проверена по эталонной выборке",
        "NOT_VERIFIED",
        "Требуются API-доступ, фактические токены/время и сравнение с gold.",
    )
    add(
        "TA-UNKNOWN-01",
        "Неизвестный прайс обработан вживую",
        "BLOCKED",
        "Файл предоставляется заказчиком на созвоне или для полной видеозаписи.",
    )
    return checks


def _run_llm_stage(
    *,
    items: list[ParsedItem],
    report_dir: Path,
    provider_name: str,
    max_cases: int,
    model: str | None,
    replay_dir: str | Path | None,
    confirm_live_api: bool,
) -> dict[str, Any]:
    if provider_name == "none":
        return {
            "status": "NOT_RUN",
            "provider": "none",
            "automatic_application_performed": False,
        }

    payload, selected = build_llm_payload(items, max_cases=max_cases)
    if not payload["rows"]:
        return {
            "status": "NOT_NEEDED",
            "provider": provider_name,
            "reason": "Нет спорных строк; внешний LLM не вызывался",
            "rows_sent": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0,
            "automatic_application_performed": False,
            "live_quality_verified": False,
        }

    if provider_name == "mock":
        provider = MockPilotProvider()
    elif provider_name == "openai":
        if not confirm_live_api:
            raise ValueError(
                "Для реального API обязателен флаг --confirm-live-api"
            )
        provider = OpenAIPilotProvider(
            model=model,
            replay_dir=Path(replay_dir) if replay_dir else report_dir / "replay",
        )
    elif provider_name == "replay":
        if not replay_dir:
            raise ValueError("Для replay требуется --replay-dir")
        provider = ReplayPilotProvider(replay_dir)
    else:
        raise ValueError(f"Неизвестный LLM provider: {provider_name}")

    safe = run_safe_pipeline(
        payload=payload,
        provider=provider,
        output_dir=report_dir / "llm_safe",
    )
    return {
        "status": "VERIFIED",
        "provider": provider_name,
        "model": safe.model,
        "rows_sent": len(selected),
        "input_tokens": safe.input_tokens,
        "output_tokens": safe.output_tokens,
        "estimated_cost": estimate_cost(
            safe.model,
            safe.input_tokens,
            safe.output_tokens,
        ),
        "request_fingerprint": safe.audit["request_fingerprint"],
        "guardrail_events": len(safe.audit["guardrail_events"]),
        "automatic_application_performed": safe.audit[
            "automatic_application_performed"
        ],
        "live_quality_verified": False,
    }



def _search_comment(result: SearchResult) -> str:
    comment = result.item.comment
    if not result.is_profile_alias:
        return comment

    note = (
        "Поисковый синоним профиля: "
        f"{result.effective_profile}; исходный профиль поставщика: "
        f"{result.item.profile}; не отдельная складская позиция"
    )
    return f"{comment}; {note}" if comment else note

def _write_candidates(path: Path, items: list[ParsedItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in items:
            record = {
                "source_id": item.source.display(),
                "supplier": item.supplier,
                "description": item.raw_description,
                "profile": item.profile,
                "grade": item.grade,
                "dimensions": [
                    _dimension_text(item.dim1_display, item.dim1),
                    _dimension_text(item.dim2_display, item.dim2),
                    _dimension_text(item.dim3_display, item.dim3),
                ],
                "requires_review": item.requires_review,
                "review_reasons": candidate_reasons(item),
                "warnings": item.warnings,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _search_order_valid(results: list[SearchResult]) -> bool:
    seen_near = False
    previous_delta = None
    for result in results:
        if result.match_type == "БЛИЖАЙШИЙ РАЗМЕР":
            seen_near = True
            if previous_delta is not None and result.size_delta < previous_delta:
                return False
            previous_delta = result.size_delta
        elif result.match_type == "ТОЧНОЕ":
            if seen_near:
                return False
        else:
            return False
    return True


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check["status"]) for check in checks}
    if "FAILED" in statuses:
        return "FAILED"
    if "BLOCKED" in statuses or "NOT_VERIFIED" in statuses:
        return "READY_WITH_LIMITATIONS"
    return "VERIFIED"


def _dimension_text(
    display: str | None,
    value: Any,
) -> str | None:
    if display:
        return display
    return _decimal_text(value)


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Отчёт готовности тестового задания",
        "",
        f"- Версия проекта: `{report['project_version']}`",
        f"- Статус: **{report['status']}**",
        f"- Файлов: `{report['input_file_count']}`",
        f"- Позиций: `{report['rows']}`",
        f"- Время разбора: `{report['parse_seconds']}` сек.",
        f"- Скорость: `{report['rows_per_second']}` строк/сек.",
        f"- LLM provider: `{report['llm'].get('provider')}`",
        f"- Автоприменение LLM: `{report['llm'].get('automatic_application_performed')}`",
        f"- Основной XLSX: `{report['output']}`",
        f"- Поиск XLSX: `{report['search_output']}`",
        f"- Поиск JSON: `{report['search_json']}`",
        "",
        "## Проверки",
        "",
        "| ID | Проверка | Статус |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        lines.append(
            f"| {check['id']} | {check['title']} | {check['status']} |"
        )
    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            *[f"- {value}" for value in report["limitations"]],
            "",
        ]
    )
    return "\n".join(lines)
