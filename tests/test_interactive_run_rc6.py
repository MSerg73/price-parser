from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from pathlib import Path

from price_parser.interactive_run import (
    ProfileResolution,
    _build_llm_payload,
    _run_llm_stage,
    build_output_rows,
    damerau_levenshtein,
    discover_price_files,
    next_output_path,
    resolve_profile_query,
    run_input_folder,
)
from price_parser.models import ParsedItem, ParseStats, SourceRef


def item(
    profile: str,
    *,
    diameter: str = "20",
    row: int = 1,
) -> ParsedItem:
    return ParsedItem(
        supplier="TEST",
        profile=profile,
        grade="БРБ2",
        dim1=Decimal(diameter),
        dim2=Decimal("1000"),
        dim3=None,
        availability="1 кг",
        price_rub_kg=None,
        comment="",
        source=SourceRef("sample.xlsx", "Лист1", row),
        raw_description=f"{profile} БрБ2 {diameter}",
        confidence=1.0,
    )


def test_empty_query_returns_all_current_rows() -> None:
    items = [item("ПРУТОК"), item("ТРУБА", row=2)]
    resolution = resolve_profile_query(items, "")
    assert resolution.status == "ALL"
    rows = build_output_rows(items, resolution)
    assert len(rows) == 2


def test_round_bar_typo_is_corrected_and_both_filter_values_are_created() -> None:
    items = [item("ПРУТОК", row=1), item("ПРУТОК", diameter="22", row=2)]
    resolution = resolve_profile_query(items, "Прутокк")
    assert resolution.status == "CORRECTED"
    assert resolution.canonical_profile == "ПРУТОК"
    assert resolution.matched_profiles == ("ПРУТОК", "КРУГ")

    rows = build_output_rows(items, resolution)
    assert len(rows) == 4
    assert {row[1] for row in rows} == {"ПРУТОК", "КРУГ"}
    assert [row[3] for row in rows].count(20) == 2
    assert [row[3] for row in rows].count(22) == 2


def test_unknown_word_returns_empty_result_without_failure() -> None:
    items = [item("ПРУТОК"), item("ТРУБА", row=2)]
    resolution = resolve_profile_query(items, "Мебель")
    assert resolution.status == "NOT_FOUND"
    assert build_output_rows(items, resolution) == []


def test_new_profile_from_current_run_is_available_without_static_dictionary() -> None:
    items = [item("КВАДРАТ")]
    exact = resolve_profile_query(items, "квадрат")
    typo = resolve_profile_query(items, "квадраат")
    assert exact.status == "EXACT"
    assert exact.canonical_profile == "КВАДРАТ"
    assert typo.status == "CORRECTED"
    assert typo.canonical_profile == "КВАДРАТ"


def test_case_is_ignored_but_unrelated_word_is_not_replaced() -> None:
    items = [item("ЛЕНТА")]
    assert resolve_profile_query(items, "лЕнТа").status == "EXACT"
    assert resolve_profile_query(items, "Мебель").status == "NOT_FOUND"


def test_transposition_counts_as_one_typo() -> None:
    assert damerau_levenshtein("ТРУАБ", "ТРУБА") == 1


def test_discovery_uses_all_supported_files_and_ignores_excel_lock(tmp_path: Path) -> None:
    for name in ("a.xls", "B.XLSX", "~$temp.xlsx", "note.txt"):
        (tmp_path / name).write_bytes(b"x")
    files = discover_price_files(tmp_path)
    assert [path.name for path in files] == ["a.xls", "B.XLSX"]


def test_output_name_is_not_overwritten(tmp_path: Path) -> None:
    assert next_output_path(tmp_path, base_name="Общая").name == "Общая.xlsx"
    (tmp_path / "Общая.xlsx").write_bytes(b"x")
    assert next_output_path(tmp_path, base_name="Общая").name == "Общая2.xlsx"


def test_run_input_folder_creates_empty_xlsx_and_zero_token_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    parsed = [item("ПРУТОК")]
    stats = ParseStats(
        input_files=1,
        raw_items=1,
        parsed_items=1,
    )

    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: (parsed, stats),
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        profile_query="Мебель",
        llm_provider="none",
    )

    assert report["status"] == "SUCCESS"
    assert report["input_file_count"] == 1
    assert report["output_rows"] == 0
    assert Path(report["output"]).name == "Мебель.xlsx"
    assert report["llm"]["input_tokens"] == 0
    assert report["llm"]["output_tokens"] == 0
    assert report["llm"]["total_tokens"] == 0

    with zipfile.ZipFile(report["output"]) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Поставщик" in sheet
        assert 'r="2"' not in sheet

    text = Path(report["report_text"]).read_text(encoding="utf-8")
    assert "Файлов обработано: 1" in text
    assert "Входные токены: 0" in text
    assert "Статус поиска: NOT_FOUND" in text


def test_round_bar_output_has_default_diameter_filter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    parsed = [item("ПРУТОК", diameter="20"), item("ПРУТОК", diameter="25", row=2)]
    stats = ParseStats(input_files=1, raw_items=2, parsed_items=2)
    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: (parsed, stats),
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        profile_query="Прутокк",
        llm_provider="none",
    )

    assert report["output_rows"] == 4
    assert report["default_size_filter"] == 20
    assert Path(report["output"]).name == "Пруток.xlsx"

    with zipfile.ZipFile(report["output"]) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        assert '<filterColumn colId="3">' in sheet
        assert '<filter val="20"/>' in sheet
        assert ">ПРУТОК<" in sheet
        assert ">КРУГ<" in sheet
        assert 'sheet name="Пруток"' in workbook


def test_full_assignment_query_creates_general_and_ranked_search_xlsx(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    parsed = [
        item("ПРУТОК", diameter="20", row=1),
        item("КРУГ", diameter="22", row=2),
        item("ТРУБА", diameter="20", row=3),
    ]
    stats = ParseStats(input_files=1, raw_items=3, parsed_items=3)
    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: (parsed, stats),
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        query="пруток БрБ2 ф20",
        llm_provider="none",
    )

    assert report["status"] == "SUCCESS"
    assert report["query_mode"] == "FULL_SEARCH"
    assert report["output_rows"] == 3
    assert report["search_result_count"] == 2
    assert report["search_exact_count"] == 1
    assert report["search_nearest_count"] == 1
    assert Path(report["output"]).name == "Общая.xlsx"
    assert Path(report["search_output"]).name.startswith("Поиск пруток БрБ2 ф20")

    with zipfile.ZipFile(report["search_output"]) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "ТОЧНОЕ" in sheet
        assert "БЛИЖАЙШИЙ РАЗМЕР" in sheet


def test_required_live_llm_blocks_none_provider(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    import pytest

    with pytest.raises(RuntimeError, match="LLM обязателен"):
        run_input_folder(
            input_dir=input_dir,
            output_dir=tmp_path / "output",
            report_root=tmp_path / "reports",
            llm_provider="none",
            require_live_llm=True,
        )


def test_required_live_llm_accepts_verified_call_and_reports_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    parsed_item = item("ПРУТОК")
    parsed_item.requires_review = True
    parsed_item.review_reasons = ["manual_review_required"]
    stats = ParseStats(input_files=1, raw_items=1, parsed_items=1)

    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: ([parsed_item], stats),
    )
    monkeypatch.setattr(
        "price_parser.interactive_run._resolve_llm_provider",
        lambda provider_name, require_live_llm: "openai",
    )
    monkeypatch.setattr(
        "price_parser.interactive_run._run_llm_stage",
        lambda **kwargs: {
            "status": "VERIFIED",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "rows_sent": 1,
            "rows_returned": 1,
            "coverage_complete": True,
            "batch_size": 10,
            "batch_count": 1,
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "estimated_cost": {"usd": 0.000225},
            "automatic_application_performed": False,
            "live_quality_verified": False,
        },
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        query="пруток БрБ2 ф20",
        llm_provider="auto",
        confirm_live_api=True,
        require_live_llm=True,
    )

    assert report["status"] == "SUCCESS"
    assert report["llm_requirement_met"] is True
    assert report["llm"]["total_tokens"] == 150
    assert Path(report["output"]).exists()
    assert Path(report["search_output"]).exists()


def test_llm_payload_prioritizes_unknown_structured_supplier_rows() -> None:
    known = item("ЛИСТ", row=1)
    known.requires_review = True
    known.review_reasons = ["business_rule_pending"]
    known.source = SourceRef("supplier_alpha.xls", "Лист1", 1)

    unknown = item("КРУГ", row=534)
    unknown.requires_review = True
    unknown.review_reasons = ["manual_review_required"]
    unknown.source = SourceRef("supplier_delta_stock.xlsx", "Лист1", 534)
    unknown.attributes["source_columns"] = {
        "Наименование": "ЭИ868(ХН60ВТ)",
        "Вид проката": "круг",
        "Диаметр": "ф105",
        "Размеры мм.": "360мм",
    }

    payload, selected = _build_llm_payload([known, unknown], max_cases=1)

    assert selected == [unknown]
    assert payload["rows"][0]["source_id"].endswith("строка 534")
    assert payload["rows"][0]["source_columns"]["Вид проката"] == "круг"


def test_llm_payload_zero_limit_selects_all_candidates() -> None:
    candidates = [item("ЛИСТ", row=index) for index in range(1, 31)]
    for candidate in candidates:
        candidate.requires_review = True
        candidate.review_reasons = ["manual_review_required"]

    payload, selected = _build_llm_payload(candidates, max_cases=0)

    assert len(selected) == 30
    assert len(payload["rows"]) == 30


def test_llm_stage_processes_every_candidate_in_batches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    candidates = [item("ЛИСТ", row=index) for index in range(1, 24)]
    for candidate in candidates:
        candidate.requires_review = True
        candidate.review_reasons = ["manual_review_required"]

    batch_sizes: list[int] = []

    def fake_safe_pipeline(*, payload, provider, output_dir):
        size = len(payload["rows"])
        batch_sizes.append(size)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            rows=[{"source_id": row["source_id"]} for row in payload["rows"]],
            model="mock-model",
            input_tokens=size * 100,
            output_tokens=size * 20,
            audit={
                "request_fingerprint": f"batch-{len(batch_sizes)}",
                "guardrail_events": [],
                "automatic_application_performed": False,
            },
        )

    monkeypatch.setattr(
        "price_parser.llm.safe_pipeline.run_safe_pipeline",
        fake_safe_pipeline,
    )
    monkeypatch.setattr(
        "price_parser.llm.pilot_runner.estimate_cost",
        lambda model, input_tokens, output_tokens: {
            "usd": (input_tokens + output_tokens) / 1_000_000
        },
    )

    summary = _run_llm_stage(
        items=candidates,
        report_dir=tmp_path,
        provider_name="mock",
        max_cases=0,
        batch_size=10,
        model=None,
        replay_dir=None,
        confirm_live_api=False,
    )

    assert batch_sizes == [10, 10, 3]
    assert summary["rows_sent"] == 23
    assert summary["rows_returned"] == 23
    assert summary["coverage_complete"] is True
    assert summary["batch_count"] == 3
    assert summary["input_tokens"] == 2300
    assert summary["output_tokens"] == 460
    assert Path(summary["batch_manifest"]).exists()


def test_llm_stage_positive_diagnostic_cap_is_not_full_coverage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    candidates = [item("ЛИСТ", row=index) for index in range(1, 6)]
    for candidate in candidates:
        candidate.requires_review = True
        candidate.review_reasons = ["manual_review_required"]

    def fake_safe_pipeline(*, payload, provider, output_dir):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            rows=[{"source_id": row["source_id"]} for row in payload["rows"]],
            model="mock-model",
            input_tokens=100,
            output_tokens=20,
            audit={
                "request_fingerprint": "limited",
                "guardrail_events": [],
                "automatic_application_performed": False,
            },
        )

    monkeypatch.setattr(
        "price_parser.llm.safe_pipeline.run_safe_pipeline",
        fake_safe_pipeline,
    )
    monkeypatch.setattr(
        "price_parser.llm.pilot_runner.estimate_cost",
        lambda model, input_tokens, output_tokens: {
            "usd": (input_tokens + output_tokens) / 1_000_000
        },
    )

    summary = _run_llm_stage(
        items=candidates,
        report_dir=tmp_path,
        provider_name="mock",
        max_cases=2,
        batch_size=10,
        model=None,
        replay_dir=None,
        confirm_live_api=False,
    )

    assert summary["candidate_rows"] == 5
    assert summary["rows_sent"] == 2
    assert summary["coverage_complete"] is False


def test_square_single_side_without_grade_runs_full_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    square_20 = item("КВАДРАТ", diameter="100", row=1)
    square_20.grade = "20"
    square_45 = item("КВАДРАТ", diameter="90", row=2)
    square_45.grade = "45"
    unrelated = item("ЛИСТ", diameter="100", row=3)

    stats = ParseStats(input_files=1, raw_items=3, parsed_items=3)
    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: ([square_20, square_45, unrelated], stats),
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        query="Квадрат 100",
        llm_provider="none",
    )

    assert report["status"] == "SUCCESS"
    # RC11+ parses the generic mask directly; it no longer rewrites a square
    # side into the legacy diameter syntax ``ф100``.
    assert report["search_query_normalized"] is None
    assert report["search_mask"]["profile"] == "КВАДРАТ"
    assert report["search_mask"]["grade"] is None
    assert report["search_mask"]["dim1"] == 100
    assert report["search_mask"]["dim2"] is None
    assert report["search_mask"]["dim3"] is None
    assert report["search_result_count"] == 2
    assert report["search_exact_count"] == 1
    assert report["search_nearest_count"] == 1
    assert Path(report["search_output"]).name.startswith("Поиск Квадрат 100")


def test_full_unknown_mask_creates_empty_search_xlsx_and_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_root = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "new.xlsx").write_bytes(b"placeholder")

    parsed = [item("КВАДРАТ", diameter="100", row=1)]
    stats = ParseStats(input_files=1, raw_items=1, parsed_items=1)
    monkeypatch.setattr(
        "price_parser.interactive_run._parse_files",
        lambda files: (parsed, stats),
    )

    report = run_input_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        report_root=report_root,
        query="Стол 1000х1500",
        llm_provider="none",
    )

    assert report["status"] == "SUCCESS"
    assert report["search_result_count"] == 0
    assert report["search_mask"]["name"] == "Стол"
    assert report["search_mask"]["dim1"] == 1000
    assert report["search_mask"]["dim2"] == 1500
    assert "Позиции не найдены." in report["warnings"]
    assert Path(report["search_output"]).is_file()

    with zipfile.ZipFile(report["search_output"]) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'r="2"' not in sheet

    text = Path(report["report_text"]).read_text(encoding="utf-8")
    assert "Результат поиска: Позиции не найдены" in text
