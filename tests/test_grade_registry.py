from decimal import Decimal

from price_parser.models import RawItem, SourceRef
from price_parser.normalization import parse_raw_item


def raw(description: str) -> RawItem:
    return RawItem(
        supplier="test",
        description=description,
        availability="10 кг",
        price=None,
        grade_hint=None,
        source=SourceRef("test.xls", "Sheet1", 1),
    )


def test_confirmed_tool_steel_after_diameter() -> None:
    item = parse_raw_item(raw("Круг д001.05 У8А серебрянка В-h9-Н ГОСТ14955"))
    assert item.grade == "У8А"
    assert item.dim1 == Decimal("1.05")


def test_minimum_order_is_not_grade() -> None:
    item = parse_raw_item(raw("Круг д1.2 У10А серебрянка h9 (мин.20кг)"))
    assert item.grade == "У10А"
    assert "Альтернативное обозначение: мин.20кг" not in item.comment


def test_verified_12x13_is_grade_not_dimensions() -> None:
    item = parse_raw_item(raw("Квадрат N060 12х13"))
    assert item.grade == "12Х13"
    assert item.dim1 == Decimal("60")
    assert item.dim2 is None


def test_first_confirmed_grade_is_primary() -> None:
    item = parse_raw_item(raw("Квадрат 200*200 20х17н2 (12х13)"))
    assert item.grade == "20Х17Н2"
    assert "Альтернативная марка: 12Х13" in item.comment


def test_bearing_grade_is_confirmed() -> None:
    item = parse_raw_item(raw("Круг д003 ШХ15 серебрянка ГОСТ801-78"))
    assert item.grade == "ШХ15"


def test_minimum_without_confirmed_grade_is_not_grade() -> None:
    item = parse_raw_item(raw("Круг д011 неизвестный материал (мин.20кг)"))
    assert item.grade == "предпол."


def test_high_speed_steel_pattern_is_not_regressed() -> None:
    item = parse_raw_item(raw("Круг быстрорез д020 Р6М5 ГОСТ19265-73"))
    assert item.grade == "Р6М5"
