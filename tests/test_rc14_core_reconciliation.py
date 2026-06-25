from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import price_parser.interactive_run as interactive_run
from price_parser.llm.application import apply_verified_llm_results
from price_parser.llm.pilot_provider import PilotProviderResult
from price_parser.llm.safe_pipeline import validate_and_reconcile
from price_parser.models import ParseStats, ParsedItem, RawItem, SourceRef
from price_parser.normalization import extract_material_from_description, parse_raw_item
from price_parser.suppliers.generic import GenericParser
from price_parser.workbook import SheetData, WorkbookData


def _raw_titan_item() -> RawItem:
    return RawItem(
        supplier="SupplierDelta",
        description="Титан уголок 4х4 2740мм",
        availability="11,5",
        price="",
        source=SourceRef("SupplierDelta.xlsx", "Лист1", 2),
        extra={
            "profile_hint": None,
            "structured_profile_column": True,
            "structured_dimensions": {
                "primary": "4х4",
                "secondary": "2740мм",
            },
            "source_columns": {
                "description": "Титан уголок",
                "profile": None,
                "dim_primary": "4х4",
                "dimensions": "2740мм",
                "raw_row": [
                    "Титан уголок",
                    None,
                    "4х4",
                    "2740мм",
                    "11,5",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "Химки",
                ],
            },
            "availability_unit": "кг",
            "generic_parser": True,
        },
    )


def test_local_parser_restores_profile_material_and_three_dimensions() -> None:
    item = parse_raw_item(_raw_titan_item())

    assert item.profile == "УГОЛОК"
    assert item.grade == "предпол."
    assert item.dim1 == Decimal("4")
    assert item.dim2 == Decimal("4")
    assert item.dim3 == Decimal("2740")
    assert item.attributes["material"] == "ТИТАН"
    assert item.attributes["material_evidence"].lower() == "титан"
    assert "profile_unparsed" not in item.review_reasons
    assert "Материал явно указан в источнике: ТИТАН" in item.comment



def test_material_synonym_and_adjective_generation_is_disabled() -> None:
    assert extract_material_from_description("Титан уголок 4х4") == ("ТИТАН", "Титан")
    assert extract_material_from_description("Титановый уголок 4х4") == (None, None)
    assert extract_material_from_description("Titanium angle 4x4") == (None, None)
    assert extract_material_from_description("Ti angle 4x4") == (None, None)

def test_generic_parser_preserves_complete_source_row_and_headers() -> None:
    headers = [
        "Наименование",
        "Вид проката",
        "Диаметр",
        "Размеры",
        "Остаток, кг",
        "Факт",
        "Резерв",
        "Перекат",
        "Примечание",
        "Состояние",
        "Склад",
        "Склад 2",
        "Новое место",
        "Количество штук",
    ]
    row = [
        "Титан уголок",
        "",
        "4х4",
        "2740мм",
        11.5,
        "",
        "",
        "",
        "",
        "новое",
        "Химки",
        "",
        "С3П2",
        2,
    ]
    workbook = WorkbookData(
        path=Path("supplier_delta_stock.xlsx"),
        sheets=[SheetData(name="Лист1", rows=[headers, row])],
    )

    raw_items = GenericParser().extract(workbook)

    assert len(raw_items) == 1
    source = raw_items[0].extra["source_columns"]
    assert len(source["raw_row"]) == 14
    assert len(source["raw_headers"]) == 14
    assert source["by_header"]["Склад"] == "Химки"
    assert source["by_header"]["Новое место"] == "С3П2"
    assert source["by_header"]["Количество штук"] == "2"


def _payload() -> dict:
    return {
        "rows": [
            {
                "source_id": "SupplierDelta.xlsx / Лист1 / строка 2",
                "supplier": "SupplierDelta",
                "description": "Титан уголок 4х4 2740мм",
                "current_profile": "НЕ УКАЗАН",
                "current_material": None,
                "current_grade": None,
                "current_dimensions": [None, None, None],
                "source_columns": {
                    "description": "Титан уголок",
                    "dim_primary": "4х4",
                    "dimensions": "2740мм",
                },
                "domain_policy": {
                    "domain": "METAL_PRODUCT",
                    "preferred_sources": ["исходная строка"],
                    "forbidden_inferences": ["не выдумывать"],
                },
                "requires_review": True,
                "review_reasons": [
                    "profile_unparsed",
                    "unconfirmed_grade",
                    "dimension_unparsed",
                ],
            }
        ]
    }


def _verified_result(**overrides) -> PilotProviderResult:
    row = {
        "source_id": "SupplierDelta.xlsx / Лист1 / строка 2",
        "decision": "PROPOSE_CHANGE",
        "is_product": True,
        "profile": "УГОЛОК",
        "material": "ТИТАН",
        "grade": None,
        "dim1": "4",
        "dim2": "4",
        "dim3": "2740",
        "additional_info": [],
        "warnings": [],
        "confidence": 0.99,
        "evidence_basis": "SOURCE_TEXT",
        "research_required": False,
        "research_queries": [],
        "field_evidence": [
            {
                "field": "profile",
                "value": "УГОЛОК",
                "evidence": "уголок",
                "reason_code": "EXPLICIT_SOURCE_TOKEN",
                "reason": "Профиль прямо указан в описании",
            },
            {
                "field": "material",
                "value": "ТИТАН",
                "evidence": "Титан",
                "reason_code": "EXPLICIT_SOURCE_TOKEN",
                "reason": "Материал прямо указан в описании",
            },
            {
                "field": "dim1",
                "value": "4",
                "evidence": "4х4",
                "reason_code": "EXPLICIT_SOURCE_TOKEN",
                "reason": "Первая сторона",
            },
            {
                "field": "dim2",
                "value": "4",
                "evidence": "4х4",
                "reason_code": "EXPLICIT_SOURCE_TOKEN",
                "reason": "Вторая сторона",
            },
            {
                "field": "dim3",
                "value": "2740",
                "evidence": "2740мм",
                "reason_code": "EXPLICIT_SOURCE_TOKEN",
                "reason": "Длина",
            },
        ],
    }
    row.update(overrides)
    return PilotProviderResult(
        rows=[row],
        input_tokens=100,
        output_tokens=50,
        model="fake",
        response_id="resp-rc14",
    )


def test_safe_pipeline_accepts_only_source_confirmed_changes() -> None:
    safe = validate_and_reconcile(_payload(), _verified_result())

    row = safe.rows[0]
    assert row["decision"] == "PROPOSE_CHANGE"
    assert row["post_validation"]["changed_fields"] == [
        "profile",
        "material",
        "dim1",
        "dim2",
        "dim3",
    ]
    assert row["evidence_validation"]["rejected_fields"] == {}
    assert set(row["evidence_validation"]["confirmed_fields"]) == {
        "profile",
        "material",
        "dim1",
        "dim2",
        "dim3",
    }


def test_unconfirmed_material_synonym_is_forced_to_review() -> None:
    result = _verified_result(material="Ti")
    result.rows[0]["field_evidence"][1] = {
        "field": "material",
        "value": "Ti",
        "evidence": "Титан",
        "reason_code": "EXPLICIT_SOURCE_TOKEN",
        "reason": "Недопустимая подмена материала символом",
    }

    safe = validate_and_reconcile(_payload(), result)

    assert safe.rows[0]["decision"] == "REVIEW"
    assert "FIELD_EVIDENCE_NOT_CONFIRMED:material" in safe.rows[0]["warnings"]


def test_application_changes_only_verified_fields_and_writes_audit() -> None:
    parsed = ParsedItem(
        supplier="SupplierDelta",
        profile="НЕ УКАЗАН",
        grade="предпол.",
        dim1=None,
        dim2=None,
        dim3=None,
        availability="11,5 кг",
        price_rub_kg=None,
        comment="Исходное описание: Титан уголок 4х4 2740мм",
        source=SourceRef("SupplierDelta.xlsx", "Лист1", 2),
        raw_description="Титан уголок 4х4 2740мм",
        confidence=0.5,
        requires_review=True,
        review_reasons=[
            "profile_unparsed",
            "unconfirmed_grade",
            "dimension_unparsed",
        ],
    )
    safe = validate_and_reconcile(_payload(), _verified_result())

    summary = apply_verified_llm_results([parsed], safe.rows)

    assert summary["automatic_application_performed"] is True
    assert summary["applied_rows"] == 1
    assert summary["applied_fields"] == 5
    assert parsed.profile == "УГОЛОК"
    assert parsed.attributes["material"] == "ТИТАН"
    assert parsed.dim1 == Decimal("4")
    assert parsed.dim2 == Decimal("4")
    assert parsed.dim3 == Decimal("2740")
    assert "profile_unparsed" not in parsed.review_reasons
    assert "dimension_unparsed" not in parsed.review_reasons
    assert "unconfirmed_grade" in parsed.review_reasons
    assert summary["audit"][0]["before"]["profile"] == "НЕ УКАЗАН"
    assert summary["audit"][0]["after"]["profile"] == "УГОЛОК"


def test_run_builds_search_after_verified_application(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "price.xlsx").write_bytes(b"placeholder")

    parsed = ParsedItem(
        supplier="SupplierDelta",
        profile="НЕ УКАЗАН",
        grade="предпол.",
        dim1=None,
        dim2=None,
        dim3=None,
        availability="11,5 кг",
        price_rub_kg=None,
        comment="Исходное описание: Титан уголок 4х4 2740мм",
        source=SourceRef("SupplierDelta.xlsx", "Лист1", 2),
        raw_description="Титан уголок 4х4 2740мм",
        confidence=0.5,
        requires_review=True,
        review_reasons=[
            "profile_unparsed",
            "unconfirmed_grade",
            "dimension_unparsed",
        ],
    )
    safe = validate_and_reconcile(_payload(), _verified_result())

    monkeypatch.setattr(
        interactive_run,
        "_parse_files",
        lambda files: (
            [parsed],
            ParseStats(input_files=1, raw_items=1, parsed_items=1),
        ),
    )
    monkeypatch.setattr(
        interactive_run,
        "_resolve_llm_provider",
        lambda provider_name, require_live_llm: "openai",
    )
    monkeypatch.setattr(
        interactive_run,
        "_run_llm_stage",
        lambda **kwargs: {
            "status": "VERIFIED",
            "provider": "openai",
            "model": "fake",
            "candidate_rows": 1,
            "rows_sent": 1,
            "rows_returned": 1,
            "coverage_complete": True,
            "batch_size": 10,
            "batch_count": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "estimated_cost": {"usd": 0.001},
            "automatic_application_performed": False,
            "live_quality_verified": False,
            "_verified_rows": safe.rows,
        },
    )

    report = interactive_run.run_input_folder(
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        report_root=tmp_path / "reports",
        query="Титан уголок 4х4",
        llm_provider="openai",
        confirm_live_api=True,
        require_live_llm=True,
    )

    assert report["status"] == "SUCCESS"
    assert report["search_result_count"] == 1
    assert report["llm"]["automatic_application_performed"] is True
    assert report["llm"]["applied_fields"] == 5
    assert Path(report["source_audit"]).exists()
    assert Path(report["llm_application_audit"]).exists()
