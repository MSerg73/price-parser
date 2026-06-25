from decimal import Decimal

from price_parser.models import RawItem, SourceRef
from price_parser.normalization import (
    extract_dimensions,
    grade_match_key,
    normalize_grade,
    parse_price,
    parse_raw_item,
)
from price_parser.search import search_items


def raw(description: str, grade_hint: str | None = None) -> RawItem:
    return RawItem(
        supplier="test",
        description=description,
        availability="10 кг",
        price=None,
        grade_hint=grade_hint,
        source=SourceRef("test.xls", "Sheet1", 1),
    )


def test_price_cleanup() -> None:
    assert parse_price("2'090.00 руб.") == Decimal("2090")


def test_grade_equivalents() -> None:
    assert normalize_grade("C17200")[0] == "БРБ2"
    assert normalize_grade("Alloy 25")[0] == "БРБ2"
    assert normalize_grade("CuBe2")[0] == "БРБ2"


def test_rod_dimensions() -> None:
    dims = extract_dimensions("Пруток C17200 20х2000", "ПРУТОК")
    assert dims[:3] == (Decimal("20"), Decimal("2000"), None)


def test_pipe_dimensions() -> None:
    dims = extract_dimensions("Труба 12Х18Н10Т 5х1,5", "ТРУБА")
    assert dims[:3] == (Decimal("5"), Decimal("1.5"), None)


def test_search_exact_before_nearest() -> None:
    items = [
        parse_raw_item(raw("Пруток БРБ2 д=18 мм")),
        parse_raw_item(raw("Пруток БРБ2 д=20 мм")),
        parse_raw_item(raw("Пруток БРБ2 д=22 мм")),
    ]
    result = search_items(items, "пруток БрБ2 ф20")
    assert result[0].match_type == "ТОЧНОЕ"
    assert result[0].item.dim1 == Decimal("20")
    assert [r.item.dim1 for r in result[1:]] == [Decimal("18"), Decimal("22")]


def test_full_steel_grade_not_truncated() -> None:
    item = parse_raw_item(raw("Квадрат N008 12х18н10т г/к"))
    assert item.grade == "12Х18Н10Т"
    assert item.dim1 == Decimal("8")


def test_pipe_grade_and_dimensions_are_separate() -> None:
    item = parse_raw_item(raw("Труба М1 4х0,8хБУХТА ДКРНМ"))
    assert item.grade == "М1"
    assert item.dim1 == Decimal("4")
    assert item.dim2 == Decimal("0.8")


def test_fastener_size_is_not_grade() -> None:
    item = parse_raw_item(raw("нерж. Болт М12х75 DIN933 (А2-70)"))
    assert item.profile == "БОЛТ"
    assert item.grade == "А2-70"
    assert item.dim1 == Decimal("12")
    assert item.dim2 == Decimal("75")


def test_grade_layout_is_canonical_for_excel_filters() -> None:
    assert normalize_grade("\u041075")[0] == "\u041075"
    assert normalize_grade("A75")[0] == "\u041075"
    assert grade_match_key("\u041075") == grade_match_key("A75")


def test_foreign_grade_layout_is_preserved() -> None:
    assert normalize_grade("C10200")[0] == "C10200"
    assert normalize_grade("AISI 321")[0] == "AISI 321"
    assert normalize_grade("EN 1.4541")[0] == "EN 1.4541"


def test_excel_filter_values_have_one_canonical_grade() -> None:
    exported_values = {
        normalize_grade(value)[0]
        for value in ("\u041075", "A75", " a75 ")
    }
    assert exported_values == {"\u041075"}


def test_av_t1_is_split_into_grade_and_supply_state() -> None:
    grade, comments = normalize_grade("АВ.Т1")
    assert grade == "АВ"
    assert "Состояние поставки: Т1" in comments


def test_latin_ab_t1_is_split_into_cyrillic_canonical_grade() -> None:
    grade, comments = normalize_grade("AB.T1")
    assert grade == "АВ"
    assert "Состояние поставки: Т1" in comments


def test_unrelated_temper_like_grade_is_not_stripped() -> None:
    assert normalize_grade("В95Т1")[0] == "В95Т1"


def test_sheet_leading_thickness_is_dimension_one() -> None:
    item = parse_raw_item(raw("Лист т.100*1330*1740 12х18н9"))
    assert item.profile == "ЛИСТ"
    assert item.dim1 == Decimal("100")
    assert item.dim2 == Decimal("1330")
    assert item.dim3 == Decimal("1740")


def test_dimension_ranges_are_preserved_for_assignment_export() -> None:
    item = parse_raw_item(raw("Лист 10*350-380*650-665 Р6М5"))
    assert item.dim1 == Decimal("10")
    assert item.dim2 == Decimal("350")
    assert item.dim3 == Decimal("650")
    assert item.dim1_display is None
    assert item.dim2_display == "350-380"
    assert item.dim3_display == "650-665"


def test_strip_keeps_optional_third_dimension() -> None:
    item = parse_raw_item(raw("Полоса т.6*30*1400 AISI304"))
    assert item.profile == "ПОЛОСА"
    assert item.dim1 == Decimal("6")
    assert item.dim2 == Decimal("30")
    assert item.dim3 == Decimal("1400")


def test_assignment_brb2_alias_overrides_conflicting_grade_hint() -> None:
    item = parse_raw_item(
        raw(
            "Труба Alloy 25 (БрБ2) OD=48/ID=37,8 mm",
            grade_hint="Alloy 26",
        )
    )
    assert item.grade == "БРБ2"
    assert item.requires_review is True
    assert "grade_conflict" in item.review_reasons
    assert "Исходное значение колонки поставщика: Alloy 26" in item.comment


def test_tape_suffix_after_width_is_not_treated_as_range() -> None:
    item = parse_raw_item(raw("Лента 0.15*390-ПН-ПТ-О-2Б 12х18н10т"))
    assert item.dim1 == Decimal("0.15")
    assert item.dim2 == Decimal("390")
    assert item.dim3 is None


def test_four_component_waveguide_keeps_first_three_dimensions() -> None:
    item = parse_raw_item(
        raw(
            "Труба прямоуг. волноводная C10200 "
            "ID 23х10х2х3000 ТУ 48-21-57-72"
        )
    )
    assert item.dim1 == Decimal("23")
    assert item.dim2 == Decimal("10")
    assert item.dim3 == Decimal("2")
    assert "Дополнительный четвёртый размер" in item.comment


def test_last_dimension_plus_alternative_is_preserved() -> None:
    item = parse_raw_item(raw("Лист 32*1510*1900+2100 12х18н10т"))
    assert item.dim1 == Decimal("32")
    assert item.dim2 == Decimal("1510")
    assert item.dim3 == Decimal("1900")
    assert item.dim3_display == "1900+2100"


def test_multiple_dimension_sets_use_first_and_require_review() -> None:
    item = parse_raw_item(raw("Труба 102*6-10+114*8-9 12х18н10т"))
    assert item.dim1 == Decimal("102")
    assert item.dim2 == Decimal("6")
    assert item.dim2_display == "6-10"
    assert item.dim3 is None
    assert item.requires_review is True
    assert "multiple_dimension_sets" in item.review_reasons
    assert "дополнительный 114*8-9" in item.comment


def test_spaced_hyphen_between_fitting_sizes_is_not_numeric_range() -> None:
    item = parse_raw_item(raw("Переход д.325*12 - 159*8 08х18н10т"))
    assert item.dim1 == Decimal("325")
    assert item.dim2 == Decimal("12")
    assert item.dim2_display is None
    assert item.requires_review is True
    assert "дополнительный 159*8" in item.comment
