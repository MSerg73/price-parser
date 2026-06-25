from __future__ import annotations

from decimal import Decimal

from price_parser.models import RawItem, SourceRef
from price_parser.normalization import parse_raw_item
from price_parser.review_routing import route_review_queues
from price_parser.nomenclature import (
    MatchType,
    NomenclatureSearchService,
    SearchOptions,
    SearchQuery,
    SearchableItem,
    load_catalog,
    parse_dimensions,
)


def raw(
    description: str,
    availability: str = "10 кг",
    grade_hint: str | None = None,
) -> RawItem:
    return RawItem(
        supplier="Тест",
        description=description,
        availability=availability,
        price=None,
        grade_hint=grade_hint,
        source=SourceRef("test.xlsx", "Лист1", 1),
    )


def test_round_diameter_is_complete_and_weight_is_quantity() -> None:
    item = parse_raw_item(raw("Круг д020 42нкд", "15 кг"))
    assert item.profile == "КРУГ"
    assert item.dim1 == Decimal("20")
    assert item.dim2 is None
    assert item.requires_review is False
    assert item.attributes["dimension_completeness"] == "DIAMETER_ONLY_SOURCE"
    assert item.quantity_value == Decimal("15")
    assert item.quantity_unit == "КГ"


def test_70s3_is_reference_research_not_normalization_error() -> None:
    item = parse_raw_item(raw("Круг д010 70с3", "67 кг"))
    assert item.grade == "70С3"
    assert item.requires_review is False
    assert item.reference_research_required is True
    assert item.reference_status == "PROBABLE_NTD_MATCH"
    assert item.attributes["suggested_designation"] == "70С3А"
    assert any("автоматическая замена запрещена" in hint.lower() for hint in item.operator_hints)


def test_ep847_is_kept_as_supplier_designation() -> None:
    item = parse_raw_item(raw("Круг д046 ЭП847 (Ni-осн.)"))
    assert item.grade == "ЭП847"
    assert item.requires_review is False
    assert item.reference_status == "SOURCE_DESIGNATION_ONLY"
    assert "suggested_designation" not in item.attributes


def test_inch_tube_keeps_original_unit_and_reference_mm() -> None:
    item = parse_raw_item(
        raw("Труба кондиционерная 7/8 в бухтах по 15 метров (КИТАЙ)", "28 кг", "CU-DHP")
    )
    assert item.profile == "ТРУБА"
    assert item.dim1 == Decimal("0.875")
    assert item.dim1_display == "7/8"
    assert item.dim1_unit == "INCH"
    assert item.reference_dim1_mm == Decimal("22.2250")
    assert item.attributes["coil_length_m"] == "15"
    assert item.requires_review is False


def test_scrap_group_is_separate_and_operator_name_uses_gr_prefix() -> None:
    item = parse_raw_item(raw("Лом гр.Б18 (м/лом, кусок) 20Х13"))
    assert item.grade == "Б18"
    assert item.attributes["scrap_group"] == "Б18"
    assert item.attributes["source_material_grade"] == "20Х13"
    assert item.display_name == "Лом, гр. Б18, кусок, 20Х13"
    assert item.requires_review is False


def test_mesh_p32_uses_width_and_ntd_parameters_without_guessing_material() -> None:
    item = parse_raw_item(raw("Сетка фильтровальная П32 ш.1000 ГОСТ3187-76", "304 кг"))
    assert item.grade == "НЕ УКАЗАНА"
    assert item.dim1 == Decimal("1000")
    assert item.dim1_role == "WIDTH"
    assert item.attributes["mesh_designation"] == "П32"
    assert item.attributes["warp_wire_diameter_mm"] == "0.60"
    assert item.attributes["weft_wire_diameter_mm"] == "0.40"
    assert item.attributes["parameter_origin"] == "DERIVED_FROM_NTD"
    assert item.requires_review is False


def test_review_queues_are_separated() -> None:
    reference = parse_raw_item(raw("Круг д010 70с3"))
    normalization = parse_raw_item(raw("Труба М0б (неликвиды, детали, образцы)"))
    queues = route_review_queues([reference, normalization])
    assert reference not in queues.normalization_review
    assert reference in queues.reference_research
    assert normalization in queues.normalization_review


def test_fraction_dimensions_are_supported() -> None:
    assert parse_dimensions("7/8in") == (Decimal("0.875"), None, None)


def test_dimension_units_prevent_inch_mm_collision() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery(
            "ТРУБА",
            "CU-DHP",
            parse_dimensions("7/8in"),
            dimension_units=("INCH", None, None),
        ),
        [
            SearchableItem(
                id="inch",
                supplier="S",
                profile="ТРУБА",
                grade="CU-DHP",
                dimensions=parse_dimensions("7/8"),
                source_reference="x",
                dimension_units=("INCH", None, None),
            ),
            SearchableItem(
                id="mm",
                supplier="S",
                profile="ТРУБА",
                grade="CU-DHP",
                dimensions=parse_dimensions("0.875"),
                source_reference="y",
                dimension_units=("MM", None, None),
            ),
        ],
    )
    assert [result.item.id for result in response.results] == ["inch"]


def test_equal_fuzzy_candidates_are_marked_ambiguous() -> None:
    service = NomenclatureSearchService(load_catalog())
    items = [
        SearchableItem(
            id="304",
            supplier="S",
            profile="ОТВОД",
            grade="AISI 304L",
            dimensions=parse_dimensions("168"),
            source_reference="a",
        ),
        SearchableItem(
            id="904",
            supplier="S",
            profile="ОТВОД",
            grade="AISI 904L",
            dimensions=parse_dimensions("168"),
            source_reference="b",
        ),
    ]
    response = service.search(
        SearchQuery("ОТВОД", "AISI 04L", parse_dimensions("168")),
        items,
        SearchOptions(fuzzy_threshold=0.7),
    )
    assert len(response.results) == 2
    assert all(result.match_type is MatchType.FUZZY for result in response.results)
    assert all(
        any("AMBIGUOUS_FUZZY_TIE" in warning for warning in result.warnings)
        for result in response.results
    )
