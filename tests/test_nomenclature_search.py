from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from price_parser.nomenclature import (
    MatchType,
    NomenclatureSearchService,
    SearchOptions,
    SearchQuery,
    SearchableItem,
    load_catalog,
    parse_dimensions,
)
from price_parser.nomenclature.models import DimensionRule, RecordStatus


def item(
    item_id: str,
    grade: str,
    dimensions: str,
    profile: str = "ТРУБА",
) -> SearchableItem:
    return SearchableItem(
        id=item_id,
        supplier="Поставщик",
        profile=profile,
        grade=grade,
        dimensions=parse_dimensions(dimensions),
        source_reference=f"fixture/{item_id}",
    )


def test_dimension_separators_and_decimal_comma_are_normalized() -> None:
    expected = (Decimal("5"), Decimal("1.5"), None)
    assert parse_dimensions("5x1,5") == expected
    assert parse_dimensions("5х1.5") == expected
    assert parse_dimensions("5×1,5") == expected
    assert parse_dimensions("5*1.5") == expected


def test_exact_match_has_priority_over_near_size() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10Т", parse_dimensions("5x1,5")),
        [
            item("near", "12Х18Н10Т", "5x2"),
            item("exact", "12Х18Н10Т", "5x1,5"),
        ],
    )
    assert [result.item.id for result in response.results] == ["exact"]
    assert response.results[0].match_type is MatchType.EXACT
    assert response.results[0].requires_review is False


def test_confirmed_alias_is_explainable() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ПРУТОК", "C17200", parse_dimensions("20")),
        [item("brb2", "БРБ2", "20", profile="ПРУТОК")],
    )
    result = response.results[0]
    assert result.match_type is MatchType.ALIAS
    assert result.requires_review is False
    assert any("псевдоним" in reason.lower() for reason in result.reasons)


def test_proposed_equivalence_never_becomes_confirmed() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ЛИСТ", "12Х18Н10Т", parse_dimensions("2")),
        [item("aisi", "AISI 321", "2", profile="ЛИСТ")],
    )
    result = response.results[0]
    assert result.match_type is MatchType.PROPOSED_EQUIVALENT
    assert result.requires_review is True
    assert "автоматически" in " ".join(result.warnings).lower()


def test_proposed_equivalence_can_be_hidden() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ЛИСТ", "12Х18Н10Т", parse_dimensions("2")),
        [item("aisi", "AISI 321", "2", profile="ЛИСТ")],
        SearchOptions(include_proposed_equivalents=False, include_fuzzy=False),
    )
    assert response.results == ()


def test_fuzzy_match_is_only_a_review_candidate() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10", parse_dimensions("5x1,5")),
        [item("typo", "12Х18Н10Т", "5x1,5")],
        SearchOptions(fuzzy_threshold=0.75),
    )
    result = response.results[0]
    assert result.match_type is MatchType.FUZZY
    assert result.requires_review is True
    assert any("не подтверждает" in warning.lower() for warning in result.warnings)


def test_circle_and_bar_are_equivalent_profiles_for_search() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ПРУТОК", "БРБ2", parse_dimensions("20")),
        [
            item("bar", "БРБ2", "20", profile="ПРУТОК"),
            item("circle", "БРБ2", "20", profile="КРУГ"),
        ],
    )

    assert {result.item.id for result in response.results} == {"bar", "circle"}
    circle = next(
        result for result in response.results if result.item.id == "circle"
    )
    assert any("эквивалент" in reason.lower() for reason in circle.reasons)


def test_profile_mismatch_is_not_returned() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10Т", parse_dimensions("5")),
        [item("rod", "12Х18Н10Т", "5", profile="ПРУТОК")],
    )
    assert response.results == ()


def test_unconfigured_near_size_requires_review() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10Т", parse_dimensions("5x1,5")),
        [item("near", "12Х18Н10Т", "5x2")],
    )
    result = response.results[0]
    assert result.match_type is MatchType.NEAR_DIMENSION
    assert result.requires_review is True
    assert result.applied_dimension_rule_id is None
    assert any("не утверждены" in warning.lower() for warning in result.warnings)


def test_unconfigured_near_size_can_be_disabled() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10Т", parse_dimensions("5x1,5")),
        [item("near", "12Х18Н10Т", "5x2")],
        SearchOptions(include_unconfigured_near_dimensions=False),
    )
    assert response.results == ()


def test_confirmed_dimension_rule_is_applied_but_still_requires_manager_review() -> None:
    catalog = load_catalog()
    rule = DimensionRule(
        id="rule-pipe-test",
        profile="ТРУБА",
        max_absolute_delta=(Decimal("0"), Decimal("0.5"), None),
        status=RecordStatus.CONFIRMED,
        source_reference="Тестовое утверждённое правило",
    )
    service = NomenclatureSearchService(
        replace(catalog, dimension_rules=(rule,))
    )
    response = service.search(
        SearchQuery("ТРУБА", "12Х18Н10Т", parse_dimensions("5x1,5")),
        [item("near", "12Х18Н10Т", "5x2")],
    )
    result = response.results[0]
    assert result.applied_dimension_rule_id == "rule-pipe-test"
    assert result.requires_review is True


def test_search_is_deterministic() -> None:
    service = NomenclatureSearchService(load_catalog())
    query = SearchQuery("ПРУТОК", "БРБ2", parse_dimensions("20"))
    inventory = [
        item("b", "БРБ2", "20", profile="ПРУТОК"),
        item("a", "БРБ2", "20", profile="ПРУТОК"),
    ]
    first = service.search(query, inventory).to_dict()
    second = service.search(query, list(reversed(inventory))).to_dict()
    assert first == second


def test_response_explicitly_disables_automatic_application() -> None:
    response = NomenclatureSearchService(load_catalog()).search(
        SearchQuery("ПРУТОК", "БРБ2", parse_dimensions("20")),
        [item("one", "БРБ2", "20", profile="ПРУТОК")],
    )
    assert response.to_dict()["automatic_application_performed"] is False


def test_fuzzy_search_considers_confirmed_alias_spellings() -> None:
    service = NomenclatureSearchService(load_catalog())
    response = service.search(
        SearchQuery("ПРУТОК", "C1720O", parse_dimensions("20")),
        [item("brb2", "БРБ2", "20", profile="ПРУТОК")],
        SearchOptions(fuzzy_threshold=0.75),
    )
    result = response.results[0]
    assert result.match_type is MatchType.FUZZY
    assert result.requires_review is True
    assert any("C17200" in reason for reason in result.reasons)
