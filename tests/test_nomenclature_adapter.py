from decimal import Decimal

from price_parser.models import ParsedItem, SourceRef
from price_parser.nomenclature.repository import from_parsed_items


def test_parsed_item_adapter_preserves_traceability() -> None:
    parsed = ParsedItem(
        supplier="Supplier",
        profile="ТРУБА",
        grade="М1",
        dim1=Decimal("5"),
        dim2=Decimal("1"),
        dim3=None,
        availability="10 кг",
        price_rub_kg=Decimal("500"),
        comment="test",
        source=SourceRef("source.xlsx", "Лист1", 7),
        raw_description="Труба М1 5x1",
    )
    item = from_parsed_items([parsed])[0]
    assert item.source_reference == "source.xlsx / Лист1 / строка 7"
    assert item.payload["raw_description"] == "Труба М1 5x1"
    assert item.payload["price_rub_kg"] == "500"
