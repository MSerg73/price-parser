from price_parser.query_normalization import normalize_search_query


def test_whitespace_tabs_and_nonbreaking_spaces_are_normalized() -> None:
    assert normalize_search_query("  Квадрат\t\u00a0 100  ") == "Квадрат 100"


def test_dimension_separators_are_not_rewritten_before_grade_detection() -> None:
    assert normalize_search_query("Круг 12Х18Н10Т ф20") == "Круг 12Х18Н10Т ф20"


def test_spaces_around_dimension_separator_are_preserved_semantically() -> None:
    assert normalize_search_query("Профиль  150 х  60") == "Профиль 150 х 60"


def test_empty_value_is_safe() -> None:
    assert normalize_search_query(None) == ""
