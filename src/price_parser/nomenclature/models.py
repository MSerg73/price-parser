from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class RecordStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"


class RelationType(StrEnum):
    EQUIVALENT = "EQUIVALENT"
    APPROXIMATE = "APPROXIMATE"


class MatchType(StrEnum):
    EXACT = "EXACT"
    ALIAS = "ALIAS"
    CONFIRMED_EQUIVALENT = "CONFIRMED_EQUIVALENT"
    PROPOSED_EQUIVALENT = "PROPOSED_EQUIVALENT"
    FUZZY = "FUZZY"
    NEAR_DIMENSION = "NEAR_DIMENSION"


@dataclass(frozen=True, slots=True)
class GradeDefinition:
    id: str
    canonical: str
    status: RecordStatus
    source_reference: str
    standard_system: str | None = None


@dataclass(frozen=True, slots=True)
class AliasDefinition:
    id: str
    alias: str
    canonical_grade_id: str
    status: RecordStatus
    source_reference: str


@dataclass(frozen=True, slots=True)
class EquivalenceDefinition:
    id: str
    source_grade_id: str
    target_grade_id: str
    relation_type: RelationType
    status: RecordStatus
    source_reference: str
    bidirectional: bool = True


@dataclass(frozen=True, slots=True)
class DimensionRule:
    id: str
    profile: str
    max_absolute_delta: tuple[Decimal | None, Decimal | None, Decimal | None]
    status: RecordStatus
    source_reference: str


@dataclass(frozen=True, slots=True)
class GradeResolution:
    raw: str
    canonical_grade_id: str | None
    canonical: str | None
    resolution_type: str
    status: RecordStatus | None
    source_reference: str | None


@dataclass(frozen=True, slots=True)
class SearchQuery:
    profile: str
    grade: str
    dimensions: tuple[Decimal | None, Decimal | None, Decimal | None] = (
        None,
        None,
        None,
    )
    source_reference: str | None = None
    dimension_units: tuple[str | None, str | None, str | None] = (
        None,
        None,
        None,
    )


@dataclass(frozen=True, slots=True)
class SearchableItem:
    id: str
    supplier: str
    profile: str
    grade: str
    dimensions: tuple[Decimal | None, Decimal | None, Decimal | None]
    source_reference: str
    payload: dict[str, Any] = field(default_factory=dict)
    dimension_units: tuple[str | None, str | None, str | None] = (
        None,
        None,
        None,
    )


@dataclass(frozen=True, slots=True)
class SearchResult:
    item: SearchableItem
    match_type: MatchType
    score: float
    grade_score: float
    dimension_score: float | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    requires_review: bool
    matched_grade: str
    relation_id: str | None = None
    applied_dimension_rule_id: str | None = None
    dimension_deltas: tuple[Decimal | None, Decimal | None, Decimal | None] = (
        None,
        None,
        None,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": {
                "id": self.item.id,
                "supplier": self.item.supplier,
                "profile": self.item.profile,
                "grade": self.item.grade,
                "dimensions": [_decimal(value) for value in self.item.dimensions],
                "source_reference": self.item.source_reference,
                "payload": self.item.payload,
                "dimension_units": list(self.item.dimension_units),
            },
            "match_type": self.match_type.value,
            "score": round(self.score, 6),
            "grade_score": round(self.grade_score, 6),
            "dimension_score": (
                None if self.dimension_score is None else round(self.dimension_score, 6)
            ),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "requires_review": self.requires_review,
            "matched_grade": self.matched_grade,
            "relation_id": self.relation_id,
            "applied_dimension_rule_id": self.applied_dimension_rule_id,
            "dimension_deltas": [_decimal(value) for value in self.dimension_deltas],
        }


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: SearchQuery
    normalized_profile: str
    normalized_grade_key: str
    catalog_version: str
    results: tuple[SearchResult, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": {
                "profile": self.query.profile,
                "grade": self.query.grade,
                "dimensions": [_decimal(value) for value in self.query.dimensions],
                "source_reference": self.query.source_reference,
                "dimension_units": list(self.query.dimension_units),
            },
            "normalized": {
                "profile": self.normalized_profile,
                "grade_key": self.normalized_grade_key,
            },
            "catalog_version": self.catalog_version,
            "warnings": list(self.warnings),
            "results": [result.to_dict() for result in self.results],
            "automatic_application_performed": False,
        }


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
