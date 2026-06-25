from __future__ import annotations

from decimal import Decimal

from price_parser.models import ParsedItem, SourceRef
from price_parser.search import parse_search_query, search_items


def _item(
    profile: str,
    grade: str,
    dimensions: tuple[str, ...],
    *,
    row: int,
    raw_description: str,
    comment: str = "",
) -> ParsedItem:
    values = list(dimensions) + [None, None, None]
    return ParsedItem(
        supplier="TEST",
        profile=profile,
        grade=grade,
        dim1=Decimal(values[0]) if values[0] is not None else None,
        dim2=Decimal(values[1]) if values[1] is not None else None,
        dim3=Decimal(values[2]) if values[2] is not None else None,
        availability="1 кг",
        price_rub_kg=None,
        comment=comment,
        source=SourceRef("sample.xlsx", "Лист1", row),
        raw_description=raw_description,
        confidence=1.0,
    )


def _inventory() -> list[ParsedItem]:
    return [
        _item(
            "ПРУТОК",
            "БРБ2",
            ("20",),
            row=1,
            raw_description="Пруток C17200 ф20",
        ),
        _item(
            "КРУГ",
            "БРБ2",
            ("22",),
            row=2,
            raw_description="Круг CuBe2 ф22",
        ),
        _item(
            "КВАДРАТ",
            "20",
            ("100",),
            row=3,
            raw_description="Квадрат N100 сталь 20",
        ),
        _item(
            "КВАДРАТ",
            "45",
            ("90",),
            row=4,
            raw_description="Квадрат N90 сталь 45",
        ),
        _item(
            "ТРУБА",
            "09Г2С",
            ("150", "60"),
            row=5,
            raw_description="Профильная труба 09Г2С 150х60",
        ),
        _item(
            "АНОД",
            "CU-DHP",
            ("25",),
            row=6,
            raw_description=(
                "Аноды шариковые Cu-DHP (АМФ) Ø 25 мм "
                "(коробки по 25 кг)"
            ),
        ),
        _item(
            "АНОД",
            "CU-DXP",
            ("10", "100", "1600"),
            row=7,
            raw_description="Аноды плоские Cu-DXP (АМФ) 10 х 100 х 1600 мм",
        ),
    ]


def test_attached_name_and_two_dimensions_are_parsed() -> None:
    parsed = parse_search_query("Пруток150х90", _inventory())
    assert parsed.profile == "ПРУТОК"
    assert parsed.grade is None
    assert parsed.dimensions == (Decimal("150"), Decimal("90"))


def test_single_number_is_dim1_for_any_profile() -> None:
    parsed = parse_search_query("Квадрат 100", _inventory())
    assert parsed.profile == "КВАДРАТ"
    assert parsed.grade is None
    assert parsed.dimensions == (Decimal("100"),)

    results = search_items(_inventory(), "Квадрат 100")
    assert [result.item.source.row for result in results] == [3, 4]
    assert results[0].match_type == "ТОЧНОЕ"
    assert results[1].match_type == "БЛИЖАЙШИЙ РАЗМЕР"


def test_name_grade_and_three_dimensions_are_optional_mask_fields() -> None:
    parsed = parse_search_query(
        "Аноды плоские Cu-DXP 10 x 100 x 1600",
        _inventory(),
    )
    assert parsed.profile == "АНОД"
    assert parsed.grade == "CU-DXP"
    assert parsed.dimensions == (
        Decimal("10"),
        Decimal("100"),
        Decimal("1600"),
    )
    results = search_items(_inventory(), parsed.raw)
    assert [result.item.source.row for result in results] == [7]


def test_full_supplier_description_does_not_treat_package_weight_as_dimension() -> None:
    query = (
        "Аноды  шариковые Cu-DHP (АМФ) Ø  25 мм "
        "(коробки по 25 кг)"
    )
    parsed = parse_search_query(query, _inventory())
    assert parsed.grade == "CU-DHP"
    assert parsed.dimensions == (Decimal("25"),)
    results = search_items(_inventory(), query)
    assert [result.item.source.row for result in results] == [6]


def test_known_grade_with_x_is_not_split_into_dimensions() -> None:
    items = _inventory() + [
        _item(
            "КРУГ",
            "12Х18Н10Т",
            ("20",),
            row=8,
            raw_description="Круг 12Х18Н10Т ф20",
        )
    ]
    parsed = parse_search_query("Круг 12Х18Н10Т ф20", items)
    assert parsed.grade == "12Х18Н10Т"
    assert parsed.dimensions == (Decimal("20"),)
    assert [result.item.source.row for result in search_items(items, parsed.raw)] == [8]


def test_profile_and_grade_may_be_omitted() -> None:
    results = search_items(_inventory(), "150х60")
    assert [result.item.source.row for result in results] == [5, 7]
    assert results[0].match_type == "ТОЧНОЕ"
    assert results[1].match_type == "БЛИЖАЙШИЙ РАЗМЕР"

    results = search_items(_inventory(), "БрБ2 ф20")
    assert [result.item.source.row for result in results] == [1, 2]


def test_profile_only_and_grade_only_queries_do_not_fail() -> None:
    assert [r.item.source.row for r in search_items(_inventory(), "Квадрат")] == [3, 4]
    assert [r.item.source.row for r in search_items(_inventory(), "Cu-DHP")] == [6]


def test_explicit_numeric_grade_is_not_confused_with_side() -> None:
    results = search_items(_inventory(), "Квадрат ст.20 100")
    assert [result.item.source.row for result in results] == [3]


def test_unknown_product_returns_empty_list_without_exception() -> None:
    assert search_items(_inventory(), "Стол 1000х1500") == []



def test_grade_with_attached_st_prefix_is_extracted_before_dimensions() -> None:
    items = _inventory() + [
        _item(
            "КРУГ",
            "10Х11Н20Т3Р",
            ("20",),
            row=9,
            raw_description="Круг г/к ст10Х11Н20Т3Р ф20мм",
        )
    ]
    parsed = parse_search_query("Круг ст10Х11Н20Т3Р ф20", items)
    assert parsed.grade == "10Х11Н20Т3Р"
    assert parsed.dimensions == (Decimal("20"),)
    assert [result.item.source.row for result in search_items(items, parsed.raw)] == [9]


def test_queries_from_all_supplied_price_structures() -> None:
    items = _inventory() + [
        _item(
            "КВАДРАТ",
            "12Х18Н10Т",
            ("8",),
            row=10,
            raw_description="Квадрат N008 12х18н10т г/к о/т",
        ),
        _item(
            "ПРУТОК",
            "Л63",
            ("7",),
            row=11,
            raw_description="Пруток Л63 № 7 ДШГНП 2060-06",
        ),
        _item(
            "ЛИСТ",
            "HF",
            ("8", "100", "400"),
            row=12,
            raw_description="Мишень из Гафния / HF hafnium 99,95%",
        ),
    ]

    assert [
        result.item.source.row
        for result in search_items(items, "Квадрат 12Х18Н10Т 8")
    ] == [10]
    assert [
        result.item.source.row
        for result in search_items(items, "Пруток Л63 №7")
    ] == [11]
    assert [
        result.item.source.row
        for result in search_items(items, "Мишень Гафния 8х100х400")
    ] == [12]

def test_empty_query_is_safe_and_returns_no_results() -> None:
    assert search_items(_inventory(), "") == []
