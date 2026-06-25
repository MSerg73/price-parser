from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from price_parser.assignment import (
    build_llm_payload,
    evaluate_assignment_checks,
)
from price_parser.llm.offline_audit import run_offline_audit
from price_parser.models import ParseStats, ParsedItem, SourceRef
from price_parser.search import expand_round_bar_search_view, search_items
from price_parser.xlsx_exporter import (
    ASSIGNMENT_HEADERS,
    ASSIGNMENT_SEARCH_HEADERS,
    assignment_output_row,
    export_assignment_search_xlsx,
    export_assignment_xlsx,
)


def item(
    *,
    diameter: str = "20",
    price: str | None = None,
    grade: str = "БРБ2",
    profile: str = "ПРУТОК",
    requires_review: bool = False,
    row: int = 1,
) -> ParsedItem:
    return ParsedItem(
        supplier="Test",
        profile=profile,
        grade=grade,
        dim1=Decimal(diameter),
        dim2=None,
        dim3=None,
        availability="10 кг",
        price_rub_kg=Decimal(price) if price is not None else None,
        comment="НДС в исходном файле не указан",
        source=SourceRef(file="test.xls", sheet="Sheet1", row=row),
        raw_description=f"Пруток {grade} ф{diameter}",
        confidence=1.0,
        requires_review=requires_review,
        review_reasons=["test_review"] if requires_review else [],
    )


def test_assignment_headers_match_task_exactly() -> None:
    assert ASSIGNMENT_HEADERS == [
        "Поставщик",
        "Профиль",
        "Марка",
        "Размер 1",
        "Размер 2",
        "Размер 3",
        "Наличие",
        "Цена (₽/кг)",
        "Комментарий",
        "Источник (файл/строка)",
    ]


def test_assignment_row_uses_question_mark_for_missing_price() -> None:
    row = assignment_output_row(item())
    assert len(row) == 10
    assert row[7] == "?"
    assert row[9] == "test.xls / Sheet1 / строка 1"


def test_assignment_export_has_one_exact_ten_column_sheet(tmp_path: Path) -> None:
    output = tmp_path / "result.xlsx"
    items = [item(), item(diameter="22", row=2)]
    results = search_items(items, "пруток БрБ2 ф20")
    export_assignment_xlsx(output, items, results)

    with zipfile.ZipFile(output) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        worksheet_names = [
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet")
        ]
        xml = archive.read("xl/worksheets/sheet1.xml")

    assert workbook.count("<sheet ") == 1
    assert worksheet_names == ["xl/worksheets/sheet1.xml"]

    root = ElementTree.fromstring(xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    first_row = root.find("x:sheetData/x:row", namespace)
    values = [
        "".join(cell.itertext())
        for cell in first_row.findall("x:c", namespace)
    ]
    assert values == ASSIGNMENT_HEADERS


def test_assignment_search_is_exported_as_separate_artifact(tmp_path: Path) -> None:
    output = tmp_path / "search.xlsx"
    items = [item(), item(diameter="22", row=2)]
    results = search_items(items, "пруток БрБ2 ф20")
    export_assignment_search_xlsx(output, results)

    with zipfile.ZipFile(output) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        xml = archive.read("xl/worksheets/sheet1.xml")

    assert workbook.count("<sheet ") == 1

    root = ElementTree.fromstring(xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    first_row = root.find("x:sheetData/x:row", namespace)
    values = [
        "".join(cell.itertext())
        for cell in first_row.findall("x:c", namespace)
    ]
    assert values == ASSIGNMENT_SEARCH_HEADERS



def test_search_view_shows_bar_and_circle_for_bar_only_source(tmp_path: Path) -> None:
    output = tmp_path / "search.xlsx"
    source_results = search_items(
        [item(profile="ПРУТОК", diameter="20", row=1)],
        "пруток БрБ2 ф20",
    )
    view_results = expand_round_bar_search_view(source_results)

    assert [result.effective_profile for result in view_results] == [
        "ПРУТОК",
        "КРУГ",
    ]
    assert [result.is_profile_alias for result in view_results] == [
        False,
        True,
    ]

    export_assignment_search_xlsx(output, view_results)

    with zipfile.ZipFile(output) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert ">ПРУТОК<" in xml
    assert ">КРУГ<" in xml
    assert "не отдельная складская позиция" in xml


def test_search_view_opens_with_diameter_20_filter(tmp_path: Path) -> None:
    output = tmp_path / "search.xlsx"
    source_results = search_items(
        [
            item(profile="ПРУТОК", diameter="20", row=1),
            item(profile="ПРУТОК", diameter="22", row=2),
        ],
        "пруток БрБ2 ф20",
    )
    view_results = expand_round_bar_search_view(source_results)
    export_assignment_search_xlsx(output, view_results)

    with zipfile.ZipFile(output) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    # Размер 1 is column F in the separate search workbook; colId is zero-based.
    assert '<filterColumn colId="5">' in xml
    assert '<filter val="20"/>' in xml

    root = ElementTree.fromstring(xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = root.findall("x:sheetData/x:row", namespace)
    visible = [row for row in rows[1:] if row.get("hidden") != "1"]
    hidden = [row for row in rows[1:] if row.get("hidden") == "1"]

    # Exact Ø20 is visible under both labels; nearest Ø22 stays in the table
    # but is hidden by the saved default filter.
    assert len(visible) == 2
    assert len(hidden) == 2


def test_main_assignment_rows_are_not_duplicated_by_search_aliases(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.xlsx"
    items = [item(profile="ПРУТОК", diameter="20", row=1)]
    source_results = search_items(items, "пруток БрБ2 ф20")
    view_results = expand_round_bar_search_view(source_results)

    export_assignment_xlsx(output, items, view_results)

    with zipfile.ZipFile(output) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")

    root = ElementTree.fromstring(xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = root.findall("x:sheetData/x:row", namespace)
    assert len(rows) == 2  # header + one canonical source item


def test_search_exact_precedes_nearest() -> None:
    items = [
        item(diameter="22", row=2),
        item(diameter="20", row=1),
        item(diameter="18", row=3),
    ]
    results = search_items(items, "пруток БрБ2 ф20")
    assert results[0].match_type == "ТОЧНОЕ"
    assert results[0].size_delta == 0
    assert [result.size_delta for result in results[1:]] == [
        Decimal("2"),
        Decimal("2"),
    ]


def test_assignment_search_includes_circle_and_bar_profiles() -> None:
    items = [
        item(profile="КРУГ", diameter="20", row=2),
        item(profile="ПРУТОК", diameter="20", row=1),
        item(profile="КРУГ", diameter="22", row=3),
        item(profile="ТРУБА", diameter="20", row=4),
    ]

    results = search_items(items, "пруток БрБ2 ф20")

    assert [result.item.profile for result in results] == [
        "ПРУТОК",
        "КРУГ",
        "КРУГ",
    ]
    assert [result.match_type for result in results] == [
        "ТОЧНОЕ",
        "ТОЧНОЕ",
        "БЛИЖАЙШИЙ РАЗМЕР",
    ]


def test_assignment_search_circle_query_is_reciprocal() -> None:
    items = [
        item(profile="ПРУТОК", diameter="20", row=2),
        item(profile="КРУГ", diameter="20", row=1),
    ]

    results = search_items(items, "круг БрБ2 ф20")

    assert [result.item.profile for result in results] == ["КРУГ", "ПРУТОК"]


def test_assignment_llm_payload_is_minimal_and_bounded() -> None:
    items = [
        item(row=index, requires_review=True)
        for index in range(1, 31)
    ]
    payload, selected = build_llm_payload(items, max_cases=25)
    assert len(selected) == 25
    assert len(payload["rows"]) == 25
    assert set(payload["rows"][0]) == {
        "source_id",
        "supplier",
        "description",
        "current_profile",
        "current_grade",
        "current_dimensions",
        "domain_policy",
        "requires_review",
        "review_reasons",
    }


def test_assignment_llm_payload_is_empty_when_review_is_not_needed() -> None:
    payload, selected = build_llm_payload(
        [item(row=1), item(row=2)],
        max_cases=25,
    )

    assert selected == []
    assert payload == {"rows": []}


def test_offline_llm_audit_is_fully_verified(tmp_path: Path) -> None:
    report = run_offline_audit(tmp_path)
    assert report["status"] == "VERIFIED"
    assert report["checks_total"] == 19
    assert report["checks_failed"] == 0
    assert report["automatic_application_performed"] is False
    assert report["live_model_verified"] is False
    assert (tmp_path / "llm_offline_audit.json").exists()
    assert (tmp_path / "llm_offline_audit.md").exists()


def test_assignment_checklist_keeps_live_checks_unverified(tmp_path: Path) -> None:
    output = tmp_path / "result.xlsx"
    output.write_bytes(b"xlsx")
    items = [item()]
    results = search_items(items, "пруток БрБ2 ф20")
    checks = evaluate_assignment_checks(
        source_files=[Path("test.xls")],
        items=items,
        search_results=results,
        stats=ParseStats(input_files=1, raw_items=1, parsed_items=1),
        output=output,
        llm_summary={
            "status": "VERIFIED",
            "automatic_application_performed": False,
        },
    )
    by_id = {check["id"]: check for check in checks}
    assert by_id["TA-LLM-01"]["status"] == "VERIFIED"
    assert by_id["TA-LIVE-01"]["status"] == "NOT_VERIFIED"
    assert by_id["TA-UNKNOWN-01"]["status"] == "BLOCKED"


def test_assignment_row_preserves_dimension_range_text() -> None:
    ranged = item()
    ranged.dim2 = Decimal("350")
    ranged.dim3 = Decimal("650")
    ranged.dim2_display = "350-380"
    ranged.dim3_display = "650-665"

    row = assignment_output_row(ranged)

    assert row[4] == "350-380"
    assert row[5] == "650-665"
