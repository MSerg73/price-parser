from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .domain_routing import policy_payload
from .env_config import load_project_env
from .llm import candidate_reasons, collect_candidates
from .llm.application import (
    apply_verified_llm_results,
    write_application_audit,
    write_source_audit,
)
from .models import ParsedItem
from .query_normalization import normalize_search_query
from .search import parse_search_query, search_items
from .xlsx_exporter import (
    assignment_output_row,
    export_assignment_rows_xlsx,
    export_assignment_search_xlsx,
)


SUPPORTED_PRICE_SUFFIXES = {".xls", ".xlsx"}
ROUND_BAR_PROFILES = ("ПРУТОК", "КРУГ")
_FORBIDDEN_FILE_CHARS = '<>:"/\\|?*'


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    raw_query: str
    normalized_query: str
    status: str
    corrected: bool
    canonical_profile: str | None
    matched_profiles: tuple[str, ...]
    available_profiles: tuple[str, ...]
    edit_distance: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "status": self.status,
            "corrected": self.corrected,
            "canonical_profile": self.canonical_profile,
            "matched_profiles": list(self.matched_profiles),
            "available_profiles": list(self.available_profiles),
            "edit_distance": self.edit_distance,
        }


def discover_price_files(input_dir: str | Path) -> list[Path]:
    """Return all .xls/.xlsx files placed directly in the current Input folder."""
    directory = Path(input_dir)
    if not directory.exists():
        raise FileNotFoundError(f"Не найдена папка Input: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Input должен быть папкой: {directory}")

    files = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_PRICE_SUFFIXES
        and not path.name.startswith("~$")
    ]
    return sorted(files, key=lambda path: path.name.casefold())


def normalize_profile_text(value: str | None) -> str:
    text = str(value or "").replace("Ё", "Е").replace("ё", "е")
    return " ".join(text.casefold().split()).upper()


def resolve_profile_query(
    items: Iterable[ParsedItem],
    raw_query: str | None,
) -> ProfileResolution:
    """Resolve one profile query against profiles parsed during the current run.

    No historical/static profile list is used. A conservative edit-distance
    correction is applied only when the closest current profile is unique.
    """
    item_list = list(items)
    raw = str(raw_query or "").strip()
    normalized_query = normalize_profile_text(raw)

    profile_counter: Counter[str] = Counter()
    display_by_normalized: dict[str, str] = {}
    for item in item_list:
        normalized = normalize_profile_text(item.profile)
        if not normalized:
            continue
        profile_counter[normalized] += 1
        display_by_normalized.setdefault(normalized, str(item.profile).strip().upper())

    # When either round-bar spelling is present, both spellings are valid
    # search terms because the customer explicitly equated them for search.
    if any(profile in profile_counter for profile in ROUND_BAR_PROFILES):
        for profile in ROUND_BAR_PROFILES:
            display_by_normalized.setdefault(profile, profile)

    available = tuple(
        display_by_normalized[key]
        for key in sorted(
            display_by_normalized,
            key=lambda key: (-profile_counter.get(key, 0), key),
        )
    )

    if not normalized_query:
        return ProfileResolution(
            raw_query=raw,
            normalized_query="",
            status="ALL",
            corrected=False,
            canonical_profile=None,
            matched_profiles=(),
            available_profiles=available,
        )

    if normalized_query in display_by_normalized:
        canonical = display_by_normalized[normalized_query]
        matched = _matched_profile_group(canonical, display_by_normalized)
        return ProfileResolution(
            raw_query=raw,
            normalized_query=normalized_query,
            status="EXACT",
            corrected=False,
            canonical_profile=canonical,
            matched_profiles=matched,
            available_profiles=available,
            edit_distance=0,
        )

    scored: list[tuple[int, str]] = []
    for candidate in display_by_normalized:
        distance = damerau_levenshtein(normalized_query, candidate)
        if _distance_is_acceptable(
            normalized_query,
            candidate,
            distance,
        ):
            scored.append((distance, candidate))

    if scored:
        best_distance = min(distance for distance, _ in scored)
        best = sorted(
            candidate
            for distance, candidate in scored
            if distance == best_distance
        )
        if len(best) == 1:
            canonical = display_by_normalized[best[0]]
            matched = _matched_profile_group(canonical, display_by_normalized)
            return ProfileResolution(
                raw_query=raw,
                normalized_query=normalized_query,
                status="CORRECTED",
                corrected=True,
                canonical_profile=canonical,
                matched_profiles=matched,
                available_profiles=available,
                edit_distance=best_distance,
            )

    return ProfileResolution(
        raw_query=raw,
        normalized_query=normalized_query,
        status="NOT_FOUND",
        corrected=False,
        canonical_profile=None,
        matched_profiles=(),
        available_profiles=available,
    )


def build_output_rows(
    items: Iterable[ParsedItem],
    resolution: ProfileResolution,
) -> list[list[Any]]:
    values = list(items)

    if resolution.status == "ALL":
        return [assignment_output_row(item) for item in values]

    if resolution.status == "NOT_FOUND" or not resolution.canonical_profile:
        return []

    canonical = normalize_profile_text(resolution.canonical_profile)
    if canonical in ROUND_BAR_PROFILES:
        selected = [
            item
            for item in values
            if normalize_profile_text(item.profile) in ROUND_BAR_PROFILES
        ]
        labels = (
            ("КРУГ", "ПРУТОК")
            if canonical == "КРУГ"
            else ("ПРУТОК", "КРУГ")
        )
        rows: list[list[Any]] = []
        for item in selected:
            source_profile = normalize_profile_text(item.profile)
            for label in labels:
                comment = item.comment
                if label != source_profile:
                    note = (
                        "Поисковый синоним профиля: "
                        f"{label}; исходный профиль поставщика: "
                        f"{item.profile}; не отдельная складская позиция"
                    )
                    comment = f"{comment}; {note}" if comment else note
                rows.append(
                    assignment_output_row(
                        item,
                        profile_override=label,
                        comment_override=comment,
                    )
                )
        return rows

    matched = {
        normalize_profile_text(profile)
        for profile in resolution.matched_profiles
    }
    return [
        assignment_output_row(item)
        for item in values
        if normalize_profile_text(item.profile) in matched
    ]


def next_output_path(
    output_dir: str | Path,
    *,
    base_name: str,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_output_name(base_name)
    first = directory / f"{safe_name}.xlsx"
    if not first.exists():
        return first

    for index in range(2, 1_000_000):
        candidate = directory / f"{safe_name}{index}.xlsx"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Не удалось подобрать свободное имя XLSX")


def sanitize_output_name(value: str) -> str:
    cleaned = "".join(
        "_" if char in _FORBIDDEN_FILE_CHARS else char
        for char in str(value).strip()
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Результат"


def output_base_name(resolution: ProfileResolution) -> str:
    if resolution.status == "ALL":
        return "Общая"
    if resolution.canonical_profile:
        return _human_profile_name(resolution.canonical_profile)
    return _human_profile_name(resolution.raw_query) or "Результат"


def run_input_folder(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    report_root: str | Path,
    profile_query: str = "",
    query: str | None = None,
    llm_provider: str = "auto",
    llm_max_cases: int | None = 0,
    llm_batch_size: int = 10,
    llm_model: str | None = None,
    replay_dir: str | Path | None = None,
    confirm_live_api: bool = False,
    require_live_llm: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()

    env_load = load_project_env()
    resolved_provider = _resolve_llm_provider(
        llm_provider,
        require_live_llm=require_live_llm,
    )

    source_files = discover_price_files(input_dir)
    if not source_files:
        raise ValueError(f"В папке {Path(input_dir)} нет файлов .xls/.xlsx")

    stamp = started_at.strftime("%Y%m%d_%H%M%S_%f")
    report_dir = Path(report_root) / f"run_{stamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    parse_started = time.perf_counter()
    items, stats = _parse_files(source_files)
    parse_seconds = time.perf_counter() - parse_started

    full_query = str(query or "").strip()
    search_engine_query = normalize_search_query(full_query)

    candidates = collect_candidates(items)
    candidates_path = report_dir / "llm_candidates.jsonl"
    _write_candidates(candidates_path, candidates)

    llm_started = time.perf_counter()
    llm_error: str | None = None
    try:
        llm_stage = _run_llm_stage(
            items=items,
            report_dir=report_dir,
            provider_name=resolved_provider,
            max_cases=llm_max_cases,
            batch_size=llm_batch_size,
            model=llm_model,
            replay_dir=replay_dir,
            confirm_live_api=confirm_live_api,
        )
        llm_verified_rows = list(llm_stage.pop("_verified_rows", []))
        llm_summary = llm_stage
    except Exception as exc:
        llm_error = _sanitize_error(str(exc))
        llm_verified_rows = []
        llm_summary = {
            "status": "FAILED",
            "provider": resolved_provider,
            "model": llm_model,
            "candidate_rows": len(candidates),
            "rows_sent": None,
            "rows_returned": None,
            "coverage_complete": False,
            "batch_size": llm_batch_size,
            "batch_count": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "estimated_cost": None,
            "automatic_application_performed": False,
            "live_quality_verified": False,
            "error": llm_error,
        }
    llm_seconds = time.perf_counter() - llm_started

    if resolved_provider == "none":
        llm_summary.setdefault("rows_sent", 0)
        llm_summary.setdefault("input_tokens", 0)
        llm_summary.setdefault("output_tokens", 0)
        llm_summary.setdefault("total_tokens", 0)
        llm_summary.setdefault("estimated_cost", 0)
    elif (
        llm_summary.get("input_tokens") is not None
        and llm_summary.get("output_tokens") is not None
    ):
        llm_summary["total_tokens"] = int(
            llm_summary.get("input_tokens", 0) or 0
        ) + int(llm_summary.get("output_tokens", 0) or 0)

    llm_summary.setdefault("candidate_rows", len(candidates))
    llm_summary.setdefault(
        "coverage_complete",
        int(llm_summary.get("rows_sent") or 0) == len(candidates),
    )
    llm_requirement_met = (
        resolved_provider == "openai"
        and llm_summary.get("status") == "VERIFIED"
        and int(llm_summary.get("rows_sent") or 0) > 0
        and bool(llm_summary.get("coverage_complete"))
        and llm_error is None
    )
    if require_live_llm and not llm_requirement_met:
        status = "FAILED_LLM_REQUIRED"
    elif llm_error is not None:
        status = "SUCCESS_WITH_LLM_ERROR"
    else:
        status = "SUCCESS"

    application_summary: dict[str, Any] = {
        "automatic_application_performed": False,
        "applied_rows": 0,
        "applied_fields": 0,
        "review_rows": 0,
        "keep_rows": 0,
        "source_items_not_found": 0,
        "audit": [],
    }
    application_audit_path = report_dir / "llm_application_audit.json"
    if (
        llm_error is None
        and llm_summary.get("status") == "VERIFIED"
        and llm_verified_rows
    ):
        application_summary = apply_verified_llm_results(
            items,
            llm_verified_rows,
        )
    write_application_audit(
        application_audit_path,
        application_summary,
    )
    llm_summary["automatic_application_performed"] = bool(
        application_summary["automatic_application_performed"]
    )
    llm_summary["applied_rows"] = int(application_summary["applied_rows"])
    llm_summary["applied_fields"] = int(application_summary["applied_fields"])
    llm_summary["review_rows"] = int(application_summary["review_rows"])
    llm_summary["application_audit"] = str(
        application_audit_path.resolve()
    )
    llm_summary["source_evidence_validation_enabled"] = True
    llm_summary["material_synonym_generation_enabled"] = False

    source_audit_path = write_source_audit(
        report_dir / "source_audit.jsonl",
        items,
    )

    # Search and XLSX are intentionally built only after deterministic local
    # parsing and safe LLM reconciliation.
    resolution = resolve_profile_query(items, profile_query)
    query_mode = "FULL_SEARCH" if full_query else (
        "ALL" if resolution.status == "ALL" else "PROFILE_FILTER"
    )
    search_results = []
    parsed_search_query = None
    search_output_path: Path | None = None
    if full_query:
        parsed_search_query = parse_search_query(search_engine_query, items)
        search_results = search_items(
            items,
            search_engine_query,
            parsed_query=parsed_search_query,
        )
        output_rows = [assignment_output_row(item) for item in items]
        output_path = next_output_path(output_dir, base_name="Общая")
        search_output_path = next_output_path(
            output_dir,
            base_name=f"Поиск {full_query}",
        )
        default_filters = None
    else:
        output_rows = build_output_rows(items, resolution)
        output_path = next_output_path(
            output_dir,
            base_name=output_base_name(resolution),
        )
        default_filters: dict[int, Any] | None = None
        if (
            resolution.canonical_profile
            and normalize_profile_text(resolution.canonical_profile)
            in ROUND_BAR_PROFILES
            and any(_row_has_diameter_20(row) for row in output_rows)
        ):
            default_filters = {4: 20}

    export_seconds = 0.0
    if status != "FAILED_LLM_REQUIRED":
        export_started = time.perf_counter()
        export_assignment_rows_xlsx(
            output_path,
            output_rows,
            sheet_name=output_path.stem,
            default_filters=default_filters,
        )
        if full_query and search_output_path is not None:
            if search_results:
                export_assignment_search_xlsx(
                    search_output_path,
                    search_results,
                    default_diameter=None,
                )
            else:
                # A syntactically valid but unmatched query is not an error.
                # Use the proven ten-column empty export path so the operator
                # still receives an XLSX with headers.
                export_assignment_rows_xlsx(
                    search_output_path,
                    [],
                    sheet_name=search_output_path.stem,
                )
        export_seconds = time.perf_counter() - export_started

    total_seconds = time.perf_counter() - started
    finished_at = datetime.now().astimezone()

    exact_count = sum(
        1 for result in search_results if result.match_type == "ТОЧНОЕ"
    )
    nearest_count = sum(
        1 for result in search_results if result.match_type != "ТОЧНОЕ"
    )

    report: dict[str, Any] = {
        "schema_version": 5,
        "project_version": __version__,
        "status": status,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "input_dir": str(Path(input_dir).resolve()),
        "input_files": [str(path.resolve()) for path in source_files],
        "input_file_count": len(source_files),
        "parsed_rows": len(items),
        "output_rows": len(output_rows) if status != "FAILED_LLM_REQUIRED" else 0,
        "parse_stats": {
            "input_files": stats.input_files,
            "raw_items": stats.raw_items,
            "parsed_items": stats.parsed_items,
            "skipped_rows": stats.skipped_rows,
            "warnings": stats.warnings,
        },
        "query_mode": query_mode,
        "search_query": full_query or None,
        "search_query_normalized": (
            search_engine_query
            if full_query and search_engine_query != full_query
            else None
        ),
        "search_result_count": len(search_results),
        "search_exact_count": exact_count,
        "search_nearest_count": nearest_count,
        "search_mask": (
            parsed_search_query.as_dict()
            if parsed_search_query is not None
            else None
        ),
        "search_output": (
            str(search_output_path.resolve())
            if search_output_path is not None
            and status != "FAILED_LLM_REQUIRED"
            else None
        ),
        "profile_resolution": resolution.as_dict(),
        "default_size_filter": 20 if default_filters else None,
        "output": (
            str(output_path.resolve())
            if status != "FAILED_LLM_REQUIRED"
            else None
        ),
        "sheet_name": (
            output_path.stem if status != "FAILED_LLM_REQUIRED" else None
        ),
        "report_dir": str(report_dir.resolve()),
        "candidate_rows": len(candidates),
        "source_audit": str(source_audit_path.resolve()),
        "llm_application_audit": str(application_audit_path.resolve()),
        "timing": {
            "local_parse_seconds": round(parse_seconds, 3),
            "export_seconds": round(export_seconds, 3),
            "llm_seconds": round(llm_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        },
        "environment": {
            "env_file_found": env_load.found,
            "env_file": str(env_load.path) if env_load.path else None,
            "loaded_keys": list(env_load.loaded_keys),
        },
        "llm_required": require_live_llm,
        "llm_requirement_met": llm_requirement_met,
        "llm": llm_summary,
        "warnings": _report_warnings(
            resolution=resolution,
            llm_provider=resolved_provider,
            llm_error=llm_error,
        ),
    }

    if full_query and not search_results:
        report["warnings"].append("Позиции не найдены.")

    if require_live_llm and not llm_requirement_met:
        report["warnings"].append(
            "Обязательный live LLM не подтверждён; итоговый XLSX не создан."
        )

    json_path = report_dir / "processing_report.json"
    text_path = report_dir / "processing_report.txt"
    report["report_json"] = str(json_path.resolve())
    report["report_text"] = str(text_path.resolve())
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path.write_text(
        render_processing_report(report),
        encoding="utf-8",
    )
    return report


def render_processing_report(report: dict[str, Any]) -> str:
    resolution = report["profile_resolution"]
    timing = report["timing"]
    llm = report["llm"]
    environment = report.get("environment") or {}

    lines = [
        "PRICE PARSER — ОТЧЁТ ОБРАБОТКИ",
        f"Версия: {report['project_version']}",
        f"Статус: {report['status']}",
        f"Начало: {report['started_at']}",
        f"Завершение: {report['finished_at']}",
        "",
        f"Файлов обработано: {report['input_file_count']}",
        *[f"  - {path}" for path in report["input_files"]],
        f"Распознано товарных позиций: {report['parsed_rows']}",
        f"Строк в итоговом XLSX: {report['output_rows']}",
        f"Итоговый XLSX: {report.get('output') or 'не создан'}",
    ]

    if report.get("search_query"):
        lines.extend(
            [
                "",
                f"Поисковая заявка: {report['search_query']}",
                *(
                    [
                        "Внутренняя нормализация: "
                        + str(report["search_query_normalized"])
                    ]
                    if report.get("search_query_normalized")
                    else []
                ),
                *(
                    [
                        "Маска поиска: "
                        + _search_mask_text(report.get("search_mask"))
                    ]
                    if report.get("search_mask")
                    else []
                ),
                f"Результатов поиска: {report['search_result_count']}",
                *(
                    ["Результат поиска: Позиции не найдены"]
                    if report["search_result_count"] == 0
                    else []
                ),
                f"Точных совпадений: {report['search_exact_count']}",
                f"Ближайших размеров: {report['search_nearest_count']}",
                f"XLSX поиска: {report.get('search_output') or 'не создан'}",
            ]
        )
    else:
        lines.extend(
            [
                f"Лист: {report.get('sheet_name') or '-'}",
                "",
                f"Введённый профиль: {resolution['raw_query'] or '[пусто]'}",
                f"Статус поиска: {resolution['status']}",
                f"Распознано как: {resolution['canonical_profile'] or '-'}",
                "Профили отбора: "
                + (
                    ", ".join(resolution["matched_profiles"])
                    if resolution["matched_profiles"]
                    else ("все" if resolution["status"] == "ALL" else "нет")
                ),
                f"Исправлена опечатка: {'да' if resolution['corrected'] else 'нет'}",
                "Фильтр Размер 1 = 20: "
                + ("да" if report["default_size_filter"] else "нет"),
            ]
        )

    lines.extend(
        [
            "",
            f"Локальный разбор: {timing['local_parse_seconds']} сек.",
            f"Формирование XLSX: {timing['export_seconds']} сек.",
            f"LLM-этап: {timing['llm_seconds']} сек.",
            f"Общее время: {timing['total_seconds']} сек.",
            "",
            "Файл .env найден: "
            + ("да" if environment.get("env_file_found") else "нет"),
            f"LLM обязателен: {'да' if report.get('llm_required') else 'нет'}",
            "Требование LLM выполнено: "
            + ("да" if report.get("llm_requirement_met") else "нет"),
            f"LLM provider: {llm.get('provider')}",
            f"LLM status: {llm.get('status')}",
            f"LLM model: {llm.get('model') or '-'}",
            f"Спорных строк найдено: {report['candidate_rows']}",
            f"Строк передано в LLM: {_metric_text(llm.get('rows_sent'))}",
            "Полное покрытие спорных строк: "
            + ("да" if llm.get("coverage_complete") else "нет"),
            f"Размер LLM-пакета: {_metric_text(llm.get('batch_size'))}",
            f"Пакетов LLM: {_metric_text(llm.get('batch_count'))}",
            f"Входные токены: {_metric_text(llm.get('input_tokens'))}",
            f"Выходные токены: {_metric_text(llm.get('output_tokens'))}",
            f"Всего токенов: {_metric_text(llm.get('total_tokens'))}",
            "Автоприменение проверенных предложений LLM: "
            + ("да" if llm.get("automatic_application_performed") else "нет"),
            f"Строк с применёнными уточнениями: {_metric_text(llm.get('applied_rows'))}",
            f"Применённых полей: {_metric_text(llm.get('applied_fields'))}",
            "Генерация синонимов материалов: "
            + ("включена" if llm.get("material_synonym_generation_enabled") else "отключена"),
            f"Аудит исходных строк: {report.get('source_audit') or '-'}",
            f"Аудит применения LLM: {report.get('llm_application_audit') or '-'}",
        ]
    )

    if llm.get("estimated_cost") is not None:
        lines.append(f"Оценочная стоимость LLM: {llm.get('estimated_cost')}")
    if report.get("warnings"):
        lines.extend(["", "Предупреждения:"])
        lines.extend(f"  - {warning}" for warning in report["warnings"])
    if llm.get("error"):
        lines.extend(["", f"Ошибка LLM: {llm['error']}"])
    return "\n".join(lines) + "\n"


def _search_mask_text(mask: dict[str, Any] | None) -> str:
    if not mask:
        return "-"
    values = [
        f"наименование={mask.get('name') or '*'}",
        f"марка={mask.get('grade') or '*'}",
        f"размер1={mask.get('dim1') if mask.get('dim1') is not None else '*'}",
        f"размер2={mask.get('dim2') if mask.get('dim2') is not None else '*'}",
        f"размер3={mask.get('dim3') if mask.get('dim3') is not None else '*'}",
    ]
    return "; ".join(values)


def _resolve_llm_provider(
    provider_name: str,
    *,
    require_live_llm: bool,
) -> str:
    normalized = str(provider_name or "auto").strip().lower()
    if normalized == "auto":
        from .llm.policy import resolve_openai_api_key

        try:
            resolve_openai_api_key()
        except RuntimeError as exc:
            if require_live_llm:
                raise RuntimeError(
                    "LLM обязателен, но ключ не загружен из .env или окружения"
                ) from exc
            return "none"
        return "openai"

    if normalized == "none" and require_live_llm:
        raise RuntimeError(
            "LLM обязателен, но выбран provider=none"
        )
    if normalized not in {"none", "mock", "openai", "replay"}:
        raise ValueError(f"Неизвестный LLM provider: {provider_name}")
    return normalized


def _parse_files(files: Iterable[str | Path]) -> tuple[list[ParsedItem], Any]:
    # Lazy import keeps profile-resolution helpers usable without loading the
    # Excel engine until an actual parsing run starts.
    from .parser import parse_files

    return parse_files(files)


def _build_llm_payload(
    items: list[ParsedItem],
    *,
    max_cases: int | None = 0,
) -> tuple[dict[str, Any], list[ParsedItem]]:
    """Build the REVIEW payload.

    ``max_cases`` is retained only for explicit diagnostic runs:
    ``None`` or ``0`` means every REVIEW candidate. A positive value applies
    an operator-requested cap. Negative values are rejected.
    """
    if max_cases is not None and max_cases < 0:
        raise ValueError("llm_max_cases не может быть отрицательным")

    candidates = collect_candidates(items)
    # Unknown/generic supplier rows with explicit source columns are processed
    # first, then all remaining candidates keep deterministic source order.
    candidates.sort(
        key=lambda item: (
            0 if item.attributes.get("source_columns") else 1,
            str(item.source.file).casefold(),
            item.source.row,
        )
    )
    selected = (
        candidates
        if max_cases in (None, 0)
        else candidates[: int(max_cases)]
    )
    rows: list[dict[str, Any]] = []
    for item in selected:
        reasons = candidate_reasons(item)
        rows.append(
            {
                "source_id": item.source.display(),
                "supplier": item.supplier,
                "description": item.raw_description,
                "current_profile": item.profile,
                "current_material": item.attributes.get("material"),
                "current_grade": None if item.grade == "предпол." else item.grade,
                "current_dimensions": [
                    item.dim1_display or _decimal_text(item.dim1),
                    item.dim2_display or _decimal_text(item.dim2),
                    item.dim3_display or _decimal_text(item.dim3),
                ],
                "source_columns": item.attributes.get("source_columns"),
                "domain_policy": policy_payload(item.domain),
                "requires_review": bool(reasons),
                "review_reasons": reasons,
            }
        )
    return {"rows": rows}, selected


def _write_batch_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_llm_stage(
    *,
    items: list[ParsedItem],
    report_dir: Path,
    provider_name: str,
    max_cases: int | None,
    batch_size: int,
    model: str | None,
    replay_dir: str | Path | None,
    confirm_live_api: bool,
) -> dict[str, Any]:
    candidate_total = len(collect_candidates(items))
    if provider_name == "none":
        return {
            "status": "NOT_RUN",
            "provider": "none",
            "candidate_rows": candidate_total,
            "rows_sent": 0,
            "coverage_complete": candidate_total == 0,
            "batch_size": batch_size,
            "batch_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0,
            "automatic_application_performed": False,
            "live_quality_verified": False,
        }

    if batch_size < 1 or batch_size > 10:
        raise ValueError("llm_batch_size должен быть в диапазоне 1–10")

    payload, selected = _build_llm_payload(items, max_cases=max_cases)
    coverage_complete = len(selected) == candidate_total
    if not payload["rows"]:
        return {
            "status": "NOT_NEEDED",
            "provider": provider_name,
            "reason": "Нет спорных строк; внешний LLM не вызывался",
            "candidate_rows": candidate_total,
            "rows_sent": 0,
            "coverage_complete": coverage_complete,
            "batch_size": batch_size,
            "batch_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0,
            "automatic_application_performed": False,
            "live_quality_verified": False,
        }

    from .llm.pilot_provider import OpenAIPilotProvider, ReplayPilotProvider
    from .llm.pilot_runner import estimate_cost
    from .llm.safe_pipeline import MockPilotProvider, run_safe_pipeline

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

    rows = payload["rows"]
    batch_count = (len(rows) + batch_size - 1) // batch_size
    manifest_path = report_dir / "llm_batches.json"
    batch_summaries: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    guardrail_events = 0
    automatic_application_performed = False
    model_used: str | None = None
    rows_returned = 0
    verified_rows: list[dict[str, Any]] = []

    for batch_index, start in enumerate(range(0, len(rows), batch_size), start=1):
        batch_rows = rows[start : start + batch_size]
        batch_output_dir = (
            report_dir / "llm_safe" / f"batch_{batch_index:04d}"
        )
        summary: dict[str, Any] = {
            "batch": batch_index,
            "batch_count": batch_count,
            "rows_sent": len(batch_rows),
            "status": "PROCESSING",
            "output_dir": str(batch_output_dir.resolve()),
        }
        batch_summaries.append(summary)
        _write_batch_manifest(
            manifest_path,
            {
                "candidate_rows": candidate_total,
                "selected_rows": len(selected),
                "coverage_complete": coverage_complete,
                "batch_size": batch_size,
                "batch_count": batch_count,
                "batches": batch_summaries,
            },
        )
        try:
            safe = run_safe_pipeline(
                payload={"rows": batch_rows},
                provider=provider,
                output_dir=batch_output_dir,
            )
        except Exception as exc:
            summary["status"] = "FAILED"
            summary["error"] = _sanitize_error(str(exc))
            _write_batch_manifest(
                manifest_path,
                {
                    "candidate_rows": candidate_total,
                    "selected_rows": len(selected),
                    "coverage_complete": False,
                    "batch_size": batch_size,
                    "batch_count": batch_count,
                    "batches": batch_summaries,
                },
            )
            raise RuntimeError(
                f"LLM batch {batch_index}/{batch_count} завершился ошибкой: {exc}"
            ) from exc

        batch_input_tokens = int(safe.input_tokens or 0)
        batch_output_tokens = int(safe.output_tokens or 0)
        batch_rows_returned = len(safe.rows)
        total_input_tokens += batch_input_tokens
        total_output_tokens += batch_output_tokens
        rows_returned += batch_rows_returned
        verified_rows.extend(safe.rows)
        model_used = model_used or safe.model
        guardrail_events += len(safe.audit.get("guardrail_events", []))
        automatic_application_performed = (
            automatic_application_performed
            or bool(safe.audit.get("automatic_application_performed"))
        )
        summary.update(
            {
                "status": "VERIFIED",
                "rows_returned": batch_rows_returned,
                "input_tokens": batch_input_tokens,
                "output_tokens": batch_output_tokens,
                "request_fingerprint": safe.audit.get("request_fingerprint"),
            }
        )
        _write_batch_manifest(
            manifest_path,
            {
                "candidate_rows": candidate_total,
                "selected_rows": len(selected),
                "coverage_complete": coverage_complete,
                "batch_size": batch_size,
                "batch_count": batch_count,
                "batches": batch_summaries,
            },
        )

    if rows_returned != len(selected):
        raise RuntimeError(
            "LLM вернула не все строки: "
            f"ожидалось {len(selected)}, получено {rows_returned}"
        )

    return {
        "status": "VERIFIED",
        "provider": provider_name,
        "model": model_used,
        "candidate_rows": candidate_total,
        "rows_sent": len(selected),
        "rows_returned": rows_returned,
        "coverage_complete": coverage_complete,
        "batch_size": batch_size,
        "batch_count": batch_count,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "estimated_cost": estimate_cost(
            model_used,
            total_input_tokens,
            total_output_tokens,
        ),
        "batch_manifest": str(manifest_path.resolve()),
        "guardrail_events": guardrail_events,
        "automatic_application_performed": automatic_application_performed,
        "live_quality_verified": False,
        "_verified_rows": verified_rows,
    }


def _write_candidates(path: Path, items: list[ParsedItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in items:
            stream.write(
                json.dumps(
                    {
                        "source_id": item.source.display(),
                        "supplier": item.supplier,
                        "description": item.raw_description,
                        "profile": item.profile,
                        "grade": item.grade,
                        "dimensions": [
                            item.dim1_display or _decimal_text(item.dim1),
                            item.dim2_display or _decimal_text(item.dim2),
                            item.dim3_display or _decimal_text(item.dim3),
                        ],
                        "requires_review": item.requires_review,
                        "review_reasons": candidate_reasons(item),
                        "warnings": item.warnings,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def damerau_levenshtein(left: str, right: str) -> int:
    """Optimal-string-alignment distance with adjacent transpositions."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    rows = len(left) + 1
    cols = len(right) + 1
    matrix = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        matrix[i][0] = i
    for j in range(cols):
        matrix[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
            if (
                i > 1
                and j > 1
                and left[i - 1] == right[j - 2]
                and left[i - 2] == right[j - 1]
            ):
                matrix[i][j] = min(
                    matrix[i][j],
                    matrix[i - 2][j - 2] + 1,
                )
    return matrix[-1][-1]


def _matched_profile_group(
    canonical: str,
    available: dict[str, str],
) -> tuple[str, ...]:
    normalized = normalize_profile_text(canonical)
    if normalized in ROUND_BAR_PROFILES:
        return tuple(
            profile
            for profile in ROUND_BAR_PROFILES
            if profile in available
        )
    return (canonical,)


def _distance_is_acceptable(
    query: str,
    candidate: str,
    distance: int,
) -> bool:
    maximum_length = max(len(query), len(candidate))
    if distance <= 1:
        return True
    # Two edits are accepted only for a long word. This prevents unrelated
    # short words from being silently converted into a product profile.
    return distance == 2 and maximum_length >= 9


def _human_profile_name(value: str) -> str:
    words = [word for word in str(value).strip().split() if word]
    return " ".join(word[:1].upper() + word[1:].lower() for word in words)


def _row_has_diameter_20(row: list[Any]) -> bool:
    if len(row) < 4:
        return False
    value = row[3]
    try:
        return float(str(value).replace(",", ".")) == 20.0
    except (TypeError, ValueError):
        return False


def _metric_text(value: Any) -> str:
    return "не предоставлено" if value is None else str(value)


def _sanitize_error(value: str) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[REDACTED]", text)
    return text[:2000]


def _report_warnings(
    *,
    resolution: ProfileResolution,
    llm_provider: str,
    llm_error: str | None,
) -> list[str]:
    warnings: list[str] = []
    if resolution.status == "NOT_FOUND":
        warnings.append(
            "Введённое слово не совпало с профилями текущего запуска; "
            "создан пустой XLSX с заголовками."
        )
    if resolution.status == "CORRECTED":
        warnings.append(
            f"Ввод «{resolution.raw_query}» исправлен до "
            f"«{resolution.canonical_profile}»."
        )
    if llm_provider == "none":
        warnings.append(
            "API-ключ LLM не обнаружен: внешний LLM не вызывался."
        )
    if llm_error:
        warnings.append(
            "Локальный XLSX создан, но LLM-этап завершился ошибкой."
        )
    return warnings
