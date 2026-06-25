from pathlib import Path
import json

from price_parser.normalization import canonical_profile, search_profile_variants
from price_parser.llm.pilot_provider import PILOT_SYSTEM_INSTRUCTIONS
from price_parser.llm.openai_provider import SYSTEM_INSTRUCTIONS


def test_circle_and_bar_source_labels_are_preserved() -> None:
    assert canonical_profile("Круг д040 ЭИ66") == "КРУГ"
    assert canonical_profile("Кругляк 50 мм") == "КРУГ"
    assert canonical_profile("Пруток БрБ2 ф20") == "ПРУТОК"


def test_circle_and_bar_are_equivalent_for_search() -> None:
    assert search_profile_variants("ПРУТОК") == frozenset({"ПРУТОК", "КРУГ"})
    assert search_profile_variants("КРУГ") == frozenset({"ПРУТОК", "КРУГ"})
    assert search_profile_variants("ТРУБА") == frozenset({"ТРУБА"})


def test_llm_prompts_preserve_source_profile_and_shared_dimensions() -> None:
    for prompt in (PILOT_SYSTEM_INSTRUCTIONS, SYSTEM_INSTRUCTIONS):
        lowered = prompt.lower()
        assert "сохраняй исходное название профиля" in lowered
        assert "для обоих dim1 — диаметр" in lowered
        assert "dim2" in lowered


def test_rc3_gold_uses_circle_profile() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = [
        json.loads(line)
        for line in (root / "pilot" / "pilot_gold_v0_7_1_rc3.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    row = next(value for value in rows if value["case_id"] == "CTRL-003")
    assert row["profile"] == "КРУГ"



def test_circle_uses_diameter_and_length() -> None:
    from price_parser.models import RawItem, SourceRef
    from price_parser.normalization import parse_raw_item

    item = parse_raw_item(
        RawItem(
            supplier="Test",
            description="Круг 240×115 12Х18Н10Т",
            price=None,
            availability=None,
            source=SourceRef("test.xlsx", "Sheet1", 1),
        )
    )
    assert item.profile == "КРУГ"
    assert str(item.dim1) == "240"
    assert str(item.dim2) == "115"
    assert item.attributes["dimension_completeness"] == "DIAMETER_AND_LENGTH"
    assert item.attributes["round_bar_length_mm"] == "115"


def test_prefixed_circle_diameter_keeps_following_length() -> None:
    from price_parser.models import RawItem, SourceRef
    from price_parser.normalization import parse_raw_item

    item = parse_raw_item(
        RawItem(
            supplier="Test",
            description="Круг д240×115 12Х18Н10Т",
            price=None,
            availability=None,
            source=SourceRef("test.xlsx", "Sheet1", 2),
        )
    )
    assert str(item.dim1) == "240"
    assert str(item.dim2) == "115"
