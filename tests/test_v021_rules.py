from decimal import Decimal

from price_parser.llm.enrichment import candidate_reasons
from price_parser.models import RawItem, SourceRef
from price_parser.normalization import parse_raw_item


def raw(
    description: str,
    *,
    grade_hint: str | None = None,
    supplier: str = "test",
) -> RawItem:
    return RawItem(
        supplier=supplier,
        description=description,
        availability="10 кг",
        price=None,
        grade_hint=grade_hint,
        source=SourceRef("test.xls", "Sheet1", 1),
    )


def test_assignment_alias_wins_but_grade_conflict_is_preserved_and_flagged() -> None:
    item = parse_raw_item(
        raw(
            "Труба Alloy 25 (БрБ2) OD=48/ID=37,8 mm (длина 1090 mm)",
            grade_hint="Alloy 26",
        )
    )
    assert item.grade == "БРБ2"
    assert any(warning.startswith("Конфликт марки:") for warning in item.warnings)
    assert "grade_conflict" in candidate_reasons(item)
    assert "Исходное значение колонки поставщика: Alloy 26" in item.comment


def test_od_id_is_converted_to_outer_diameter_and_wall() -> None:
    item = parse_raw_item(
        raw("Труба Alloy 25 (БрБ2) OD=48/ID=37,8 mm (длина 1700 mm)")
    )
    assert item.grade == "БРБ2"
    assert item.dim1 == Decimal("48")
    assert item.dim2 == Decimal("5.1")
    assert item.dim3 == Decimal("1700")
    assert "Толщина стенки рассчитана из OD/ID" in item.comment
    assert candidate_reasons(item) == []


def test_composite_grade_notation_uses_first_grade() -> None:
    item = parse_raw_item(raw("Лист 08*1010*1500 08(12)х18н10т"))
    assert item.grade == "08Х18Н10Т"
    assert "Альтернативная марка: 12Х18Н10Т" in item.comment


def test_new_verified_high_speed_grade() -> None:
    item = parse_raw_item(raw("Лист 4*600*1080 11Р3АМ3Ф2"))
    assert item.grade == "11Р3АМ3Ф2"


def test_new_verified_nickel_tube_grade() -> None:
    item = parse_raw_item(raw("Трубка ДКРНТ 2.5*0.10 НК0,2Э ГОСТ13548-77"))
    assert item.grade == "НК0,2Э"


def test_new_verified_heat_resistant_grade_does_not_become_dimension() -> None:
    item = parse_raw_item(raw("Проволока д.8.0 х23ю5т ГОСТ 12766.4-90"))
    assert item.grade == "Х23Ю5Т"
    assert item.dim1 == Decimal("8.0")
    assert item.dim2 is None
    assert item.dim3 is None


def test_repeated_supplier_grade_with_local_equivalent_is_not_conflict() -> None:
    item = parse_raw_item(
        raw(
            "Лист Cu-ETP (М1) 1 х 1000 х 2000",
            grade_hint="Cu-ETP",
        )
    )
    assert item.grade == "CU-ETP"
    assert not any(warning.startswith("Конфликт марки:") for warning in item.warnings)


def test_mismatched_supplier_grade_is_conflict() -> None:
    item = parse_raw_item(
        raw(
            "Полый проводник С11000 (М1) OD 26х20 ID 12 мм",
            grade_hint="C10200",
        )
    )
    assert any(warning.startswith("Конфликт марки:") for warning in item.warnings)


def test_cu_etp_suffix_moves_to_comment() -> None:
    item = parse_raw_item(
        raw(
            "Лист Cu-ETP (М1) 4 х 1000 х 2000",
            grade_hint="Cu-ETP B1011(F)",
        )
    )
    assert item.grade == "CU-ETP"
    assert "Дополнительное обозначение марки: B1011(F)" in item.comment
