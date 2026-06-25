from __future__ import annotations

from difflib import SequenceMatcher
from decimal import Decimal

from .models import MatchType


_GRADE_BASE = {
    MatchType.EXACT: 1.0,
    MatchType.ALIAS: 0.97,
    MatchType.CONFIRMED_EQUIVALENT: 0.90,
    MatchType.PROPOSED_EQUIVALENT: 0.72,
    MatchType.FUZZY: 0.0,
    MatchType.NEAR_DIMENSION: 0.0,
}


def fuzzy_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    length_penalty = min(len(left), len(right)) / max(len(left), len(right))
    prefix_bonus = 0.03 if left[:2] == right[:2] else 0.0
    score = (ratio * 0.82) + (length_penalty * 0.15) + prefix_bonus
    return min(score, 1.0)


def grade_score(match_type: MatchType, fuzzy_score: float | None = None) -> float:
    if match_type is MatchType.FUZZY:
        return 0.0 if fuzzy_score is None else min(fuzzy_score, 0.84)
    return _GRADE_BASE[match_type]


def dimension_similarity(
    query: tuple[Decimal | None, Decimal | None, Decimal | None],
    item: tuple[Decimal | None, Decimal | None, Decimal | None],
) -> float | None:
    pairs = [
        (expected, actual)
        for expected, actual in zip(query, item, strict=True)
        if expected is not None
    ]
    if not pairs:
        return None
    if any(actual is None for _, actual in pairs):
        return 0.0

    components: list[float] = []
    for expected, actual in pairs:
        assert expected is not None and actual is not None
        if expected == actual:
            components.append(1.0)
            continue
        denominator = max(abs(expected), Decimal("0.000001"))
        relative = float(abs(expected - actual) / denominator)
        components.append(max(0.0, 1.0 - relative))
    return sum(components) / len(components)


def combined_score(grade: float, dimension: float | None) -> float:
    if dimension is None:
        return grade
    return (grade * 0.75) + (dimension * 0.25)
