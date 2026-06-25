from decimal import Decimal

from price_parser.llm.base import LLMResult
from price_parser.llm.enrichment import enrich_items
from price_parser.models import ParseStats, ParsedItem, SourceRef


class FakeProvider:
    def parse(self, payload):
        row = payload["rows"][0]
        return LLMResult(
            data={
                "rows": [
                    {
                        "source_id": row["source_id"],
                        "is_product": True,
                        "profile": "ПРУТОК",
                        "grade": "Х12М",
                        "dim1": "10",
                        "dim2": None,
                        "dim3": None,
                        "additional_info": ["Проверено тестовым провайдером"],
                        "confidence": 0.88,
                        "warnings": [],
                    }
                ]
            },
            input_tokens=100,
            output_tokens=25,
            model="fake",
        )


def test_llm_enrichment_creates_review_proposal_without_mutation() -> None:
    item = ParsedItem(
        supplier="SupplierAlpha",
        profile="ПРУТОК",
        grade="предпол.",
        dim1=Decimal("10"),
        dim2=None,
        dim3=None,
        availability="5 кг",
        price_rub_kg=None,
        comment="Исходное описание: Круг д010 х12м",
        source=SourceRef("supplier_alpha.xls", "Sheet1", 1, "левый блок"),
        raw_description="Круг д010 х12м",
        confidence=0.75,
        warnings=["Марка не распознана"],
    )
    stats = ParseStats()
    proposals = enrich_items([item], FakeProvider(), stats, batch_size=1)

    assert item.grade == "предпол."
    assert item.dim1 == Decimal("10")
    assert stats.llm_calls == 1
    assert stats.llm_input_tokens == 100
    assert "Проверено тестовым провайдером" not in item.comment
    assert proposals[0]["proposed"]["grade"] == "Х12М"
    assert proposals[0]["automatic_application_performed"] is False
