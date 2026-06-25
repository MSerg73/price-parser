from __future__ import annotations

from dataclasses import dataclass

from .models import ParsedItem


@dataclass(frozen=True, slots=True)
class ReviewQueues:
    normalization_review: tuple[ParsedItem, ...]
    reference_research: tuple[ParsedItem, ...]


def route_review_queues(items: list[ParsedItem]) -> ReviewQueues:
    """Split parsing uncertainty from reference/NTD research.

    A row can appear in both queues only when it has both independent issues.
    Reference research alone never makes a parsed row invalid.
    """
    return ReviewQueues(
        normalization_review=tuple(item for item in items if item.requires_review),
        reference_research=tuple(
            item for item in items if item.reference_research_required
        ),
    )
