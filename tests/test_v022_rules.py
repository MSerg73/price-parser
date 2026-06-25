from decimal import Decimal

from price_parser.domain_routing import classify_domain, policy_payload
from price_parser.llm.enrichment import candidate_reasons
from price_parser.models import RawItem, SourceRef
from price_parser.normalization import parse_raw_item


def raw(description: str, *, grade_hint: str | None = None) -> RawItem:
    return RawItem(
        supplier="test",
        description=description,
        availability="",
        price=None,
        grade_hint=grade_hint,
        source=SourceRef("test.xls", "Sheet1", 1),
    )


def test_scrap_group_is_not_converted_to_steel_grade() -> None:
    item = parse_raw_item(raw("Лом гр.Б26 (м/лом, кусок)"))
    assert item.profile == "ЛОМ"
    assert item.grade == "Б26"
    assert item.domain == "FERROUS_SCRAP"
    assert item.dim1 is None
    assert "business_rule_pending" not in candidate_reasons(item)
    assert item.attributes["scrap_group"] == "Б26"


def test_scrap_keeps_explicit_source_grade_only_in_comment() -> None:
    item = parse_raw_item(raw("Лом гр.Б18 (м/лом, кусок) 20Х13"))
    assert item.grade == "Б18"
    assert "20Х13" in item.comment


def test_shikhta_has_optional_dimensions() -> None:
    item = parse_raw_item(raw("Шихта 06ХН28МДТ (ЭИ943) кусок"))
    assert item.profile == "ШИХТА"
    assert "dimension_unparsed" not in candidate_reasons(item)


def test_strip_thickness_and_width() -> None:
    item = parse_raw_item(raw("Полоса т.6*30 20Х20Н14С2 (ЭИ211)"))
    assert (item.dim1, item.dim2, item.dim3) == (Decimal("6"), Decimal("30"), None)


def test_x23yu5_m_does_not_create_third_dimension() -> None:
    item = parse_raw_item(raw("Лента 1.0*10 Х23Ю5-М"))
    assert item.grade == "Х23Ю5"
    assert (item.dim1, item.dim2, item.dim3) == (Decimal("1.0"), Decimal("10"), None)
    assert "Суффикс М" in item.comment


def test_verified_ei66_alias() -> None:
    item = parse_raw_item(raw("Круг д040 ЭИ66"))
    assert item.grade == "03Х17Н14М3"
    assert item.dim1 == Decimal("40")


def test_electrotechnical_steel_alias() -> None:
    item = parse_raw_item(raw("Круг д032 сталь э/тех. 10880 (Э10) ГОСТ11036"))
    assert item.grade == "10880"
    assert "Э10" in item.comment


def test_aluminium_1340_av_t1() -> None:
    item = parse_raw_item(raw("Труба ф70*17.5 сплав алюм. (1340) АВ.Т1 ГОСТ18482-79"))
    assert item.grade == "АВ"
    assert (item.dim1, item.dim2) == (Decimal("70"), Decimal("17.5"))
    assert "Состояние поставки: Т1" in item.comment


def test_welding_wire_has_own_domain() -> None:
    item = parse_raw_item(
        raw("Проволока д.1.2 E308LT1-4(1) (BOHLER) Порошковая пров. для сварки")
    )
    assert item.grade == "E308LT1-4(1)"
    assert item.domain == "WELDING_CONSUMABLE"
    assert "AWS" in " ".join(policy_payload(item.domain)["preferred_sources"])


def test_disc_dimensions_are_parsed_but_profile_needs_customer_rule() -> None:
    item = parse_raw_item(raw("Лист 16* диам.1500мм (диск) 12Х18Н10Т"))
    assert (item.dim1, item.dim2) == (Decimal("16"), Decimal("1500"))
    assert "business_rule_pending" in candidate_reasons(item)


def test_flange_du_is_primary_size() -> None:
    item = parse_raw_item(
        raw("Фланец нерж. воротниковый 12Х18Н10Т Ду32 Ру16 ГОСТ12821-80")
    )
    assert item.dim1 == Decimal("32")
    assert "Ру16" in item.comment
    assert candidate_reasons(item) == []


def test_ring_plate_dimensions() -> None:
    item = parse_raw_item(
        raw("Плита Cu-ETP (М1) круглая 40 х d916 / d208 горячекатаная")
    )
    assert item.grade == "CU-ETP"
    assert (item.dim1, item.dim2, item.dim3) == (
        Decimal("40"), Decimal("916"), Decimal("208")
    )
    assert "business_rule_pending" in candidate_reasons(item)


def test_nd_is_non_random_length_not_outside_diameter() -> None:
    item = parse_raw_item(
        raw("Пруток C17200 (БрБ2) 32хНД ДКРПТ ГОСТ 15835-2013")
    )
    assert item.dim1 == Decimal("32")
    assert item.dim2 is None
    assert "НД — немерная длина" in item.comment


def test_unverified_explicit_designation_is_preserved_for_reference_research() -> None:
    item = parse_raw_item(raw("Круг д046 ЭП847 (Ni-осн., Cr-27.8)"))
    assert item.grade == "ЭП847"
    assert "unverified_designation" not in candidate_reasons(item)
    assert item.reference_research_required is True


def test_lead_grade_c1() -> None:
    item = parse_raw_item(raw("Лист 10*515*815 свинец (С1)"))
    assert item.grade == "С1"
    assert (item.dim1, item.dim2, item.dim3) == (
        Decimal("10"), Decimal("515"), Decimal("815")
    )


def test_cyrillic_c10200_hint_normalizes_without_false_conflict() -> None:
    item = parse_raw_item(
        raw("Труба C10200 (М0б) Ø 8 х 1 x 3000", grade_hint="С10200")
    )
    assert item.grade == "C10200"
    assert "grade_conflict" not in candidate_reasons(item)


def test_dimension_1340_is_not_aluminium_grade_without_alloy_context() -> None:
    item = parse_raw_item(raw("Лист 20*1340*1860 30ХГСА ГОСТ11269-76"))
    assert item.grade == "30ХГСА"
    assert (item.dim1, item.dim2, item.dim3) == (
        Decimal("20"), Decimal("1340"), Decimal("1860")
    )


def test_tube_size_before_grade_is_parsed_without_fraction_false_positive() -> None:
    item = parse_raw_item(raw("Труба 032*6 Х12ВМФ"))
    assert (item.dim1, item.dim2) == (Decimal("32"), Decimal("6"))
    assert "business_rule_pending" not in candidate_reasons(item)


def test_grade_fraction_is_not_treated_as_inch_size() -> None:
    item = parse_raw_item(raw("Труба 042*2.5 AISI 321 (08/12Х18Н10Т)"))
    assert (item.dim1, item.dim2) == (Decimal("42"), Decimal("2.5"))
    assert "business_rule_pending" not in candidate_reasons(item)
