from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from price_parser.normalization import search_profile_variants

from .catalog import Catalog, load_catalog
from .models import (
    EquivalenceDefinition,
    MatchType,
    RecordStatus,
    SearchQuery,
    SearchResponse,
    SearchResult,
    SearchableItem,
)
from .normalization import (
    dimension_deltas,
    dimensions_equal,
    normalize_grade_key,
    normalize_profile,
)
from .scoring import combined_score, dimension_similarity, fuzzy_similarity, grade_score


@dataclass(frozen=True, slots=True)
class SearchOptions:
    limit: int = 20
    fuzzy_threshold: float = 0.82
    include_fuzzy: bool = True
    include_proposed_equivalents: bool = True
    include_unconfigured_near_dimensions: bool = True

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 200:
            raise ValueError("limit должен быть в диапазоне 1–200")
        if not 0.0 <= self.fuzzy_threshold <= 1.0:
            raise ValueError("fuzzy_threshold должен быть в диапазоне 0–1")


class NomenclatureSearchService:
    def __init__(self, catalog: Catalog | None = None) -> None:
        self.catalog = catalog or load_catalog()

    def search(
        self,
        query: SearchQuery,
        items: list[SearchableItem],
        options: SearchOptions | None = None,
    ) -> SearchResponse:
        options = options or SearchOptions()
        profile = normalize_profile(query.profile)
        accepted_profiles = search_profile_variants(profile)
        query_key = normalize_grade_key(query.grade)
        query_resolution = self.catalog.resolve_grade(query.grade)

        grade_candidates: list[
            tuple[SearchableItem, _GradeMatch, bool]
        ] = []
        for item in items:
            item_profile = normalize_profile(item.profile)
            if item_profile not in accepted_profiles:
                continue
            grade_match = self._match_grade(
                query.grade,
                query_key,
                query_resolution.canonical_grade_id,
                item.grade,
                options,
            )
            if grade_match is not None:
                grade_candidates.append(
                    (item, grade_match, item_profile != profile)
                )

        results: list[SearchResult] = []
        exact_dimension_exists = any(
            _units_compatible(query.dimension_units, item.dimension_units)
            and dimensions_equal(query.dimensions, item.dimensions)
            for item, _, _ in grade_candidates
        )

        for item, match, equivalent_profile in grade_candidates:
            units_compatible = _units_compatible(
                query.dimension_units,
                item.dimension_units,
            )
            exact_dimensions = units_compatible and dimensions_equal(
                query.dimensions,
                item.dimensions,
            )
            if query.dimensions != (None, None, None) and not exact_dimensions:
                if exact_dimension_exists:
                    continue
                dimension_result = self._evaluate_near_dimensions(
                    profile,
                    query,
                    item,
                    options,
                )
                if dimension_result is None:
                    continue
                dimension_score_value, deltas, dimension_warnings, rule_id = dimension_result
                match_type = MatchType.NEAR_DIMENSION
                requires_review = True
                reasons = list(match.reasons)
                reasons.append("Точного размера нет; показан размерный кандидат")
                warnings = list(match.warnings) + list(dimension_warnings)
            else:
                dimension_score_value = dimension_similarity(
                    query.dimensions,
                    item.dimensions,
                )
                deltas = dimension_deltas(query.dimensions, item.dimensions)
                rule_id = None
                match_type = match.match_type
                requires_review = match.requires_review
                reasons = list(match.reasons)
                if query.dimensions != (None, None, None):
                    reasons.append("Размер совпал точно")
                warnings = list(match.warnings)

            if equivalent_profile:
                reasons.insert(
                    0,
                    (
                        f"Профиль {normalize_profile(item.profile)} учитывается "
                        f"как эквивалент {profile} для поиска"
                    ),
                )

            score = combined_score(match.grade_score, dimension_score_value)
            results.append(
                SearchResult(
                    item=item,
                    match_type=match_type,
                    score=score,
                    grade_score=match.grade_score,
                    dimension_score=dimension_score_value,
                    reasons=tuple(reasons),
                    warnings=tuple(_dedupe(warnings)),
                    requires_review=requires_review,
                    matched_grade=match.matched_grade,
                    relation_id=match.relation_id,
                    applied_dimension_rule_id=rule_id,
                    dimension_deltas=deltas,
                )
            )

        results.sort(key=_sort_key)
        results = _mark_ambiguous_fuzzy_ties(results)
        response_warnings: list[str] = []
        if any(
            "AMBIGUOUS_FUZZY_TIE" in result.warnings
            for result in results
        ):
            response_warnings.append(
                "AMBIGUOUS_FUZZY_TIE: несколько fuzzy-кандидатов имеют одинаковый score"
            )
        if not results:
            response_warnings.append("Совпадения не найдены")
        elif all(result.requires_review for result in results):
            response_warnings.append(
                "Все найденные позиции требуют ручной проверки"
            )

        return SearchResponse(
            query=query,
            normalized_profile=profile,
            normalized_grade_key=query_key,
            catalog_version=self.catalog.version,
            results=tuple(results[: options.limit]),
            warnings=tuple(response_warnings),
        )

    def _match_grade(
        self,
        query_raw: str,
        query_key: str,
        query_grade_id: str | None,
        item_raw: str,
        options: SearchOptions,
    ) -> "_GradeMatch | None":
        item_key = normalize_grade_key(item_raw)
        item_resolution = self.catalog.resolve_grade(item_raw)

        if query_key == item_key:
            return _GradeMatch(
                match_type=MatchType.EXACT,
                grade_score=grade_score(MatchType.EXACT),
                matched_grade=item_raw,
                reasons=("Марка совпала после безопасной нормализации",),
                warnings=(),
                requires_review=False,
            )

        if (
            query_grade_id is not None
            and item_resolution.canonical_grade_id == query_grade_id
        ):
            statuses = {
                self.catalog.resolve_grade(query_raw).status,
                item_resolution.status,
            }
            confirmed = statuses <= {RecordStatus.CONFIRMED}
            return _GradeMatch(
                match_type=MatchType.ALIAS,
                grade_score=grade_score(MatchType.ALIAS),
                matched_grade=item_resolution.canonical or item_raw,
                reasons=("Марки связаны подтверждённым псевдонимом",),
                warnings=(
                    ()
                    if confirmed
                    else ("Псевдоним не имеет подтверждённого статуса",)
                ),
                requires_review=not confirmed,
            )

        if (
            query_grade_id is not None
            and item_resolution.canonical_grade_id is not None
        ):
            relation = self.catalog.relation(
                query_grade_id,
                item_resolution.canonical_grade_id,
            )
            if relation is not None:
                return self._relation_match(relation, item_raw, options)

        if not options.include_fuzzy:
            return None

        fuzzy, fuzzy_value = self._best_fuzzy_score(query_key, item_raw)
        if fuzzy < options.fuzzy_threshold:
            return None
        return _GradeMatch(
            match_type=MatchType.FUZZY,
            grade_score=grade_score(MatchType.FUZZY, fuzzy),
            matched_grade=item_raw,
            reasons=(
                f"Похожее написание на {fuzzy_value}: similarity={fuzzy:.3f}",
            ),
            warnings=("Fuzzy-совпадение не подтверждает эквивалентность",),
            requires_review=True,
        )

    def _relation_match(
        self,
        relation: EquivalenceDefinition,
        item_raw: str,
        options: SearchOptions,
    ) -> "_GradeMatch | None":
        if relation.status is RecordStatus.CONFIRMED:
            requires_review = relation.relation_type.value != "EQUIVALENT"
            warnings = (
                ()
                if not requires_review
                else ("Связь подтверждена как приблизительная, а не полная",)
            )
            return _GradeMatch(
                match_type=MatchType.CONFIRMED_EQUIVALENT,
                grade_score=grade_score(MatchType.CONFIRMED_EQUIVALENT),
                matched_grade=item_raw,
                reasons=(
                    f"Найдена подтверждённая связь марок: {relation.id}",
                    f"Источник: {relation.source_reference}",
                ),
                warnings=warnings,
                requires_review=requires_review,
                relation_id=relation.id,
            )

        if (
            relation.status is RecordStatus.PROPOSED
            and options.include_proposed_equivalents
        ):
            return _GradeMatch(
                match_type=MatchType.PROPOSED_EQUIVALENT,
                grade_score=grade_score(MatchType.PROPOSED_EQUIVALENT),
                matched_grade=item_raw,
                reasons=(f"Найдена неподтверждённая связь: {relation.id}",),
                warnings=(
                    "Связь марок требует подтверждения источником",
                    "Кандидат нельзя применять автоматически",
                ),
                requires_review=True,
                relation_id=relation.id,
            )
        return None

    def _best_fuzzy_score(self, query_key: str, item_raw: str) -> tuple[float, str]:
        candidates: list[tuple[str, str]] = [
            (normalize_grade_key(item_raw), item_raw),
        ]
        resolution = self.catalog.resolve_grade(item_raw)
        if resolution.canonical_grade_id:
            for value in self.catalog.match_values_for_grade_id(
                resolution.canonical_grade_id
            ):
                candidates.append((normalize_grade_key(value), value))
        return max(
            (
                (fuzzy_similarity(query_key, candidate_key), candidate_value)
                for candidate_key, candidate_value in candidates
            ),
            key=lambda item: (item[0], item[1]),
        )

    def _evaluate_near_dimensions(
        self,
        profile: str,
        query: SearchQuery,
        item: SearchableItem,
        options: SearchOptions,
    ) -> tuple[
        float,
        tuple[Decimal | None, Decimal | None, Decimal | None],
        tuple[str, ...],
        str | None,
    ] | None:
        score = dimension_similarity(query.dimensions, item.dimensions)
        if score is None or score <= 0:
            return None

        deltas = dimension_deltas(query.dimensions, item.dimensions)
        rule = self.catalog.dimension_rule(profile)
        if rule is None:
            if not options.include_unconfigured_near_dimensions:
                return None
            return (
                score,
                deltas,
                (
                    "Для профиля не утверждены допустимые отклонения размеров",
                    "Размер показан только как кандидат для ручной проверки",
                ),
                None,
            )

        for delta, allowed, expected in zip(
            deltas,
            rule.max_absolute_delta,
            query.dimensions,
            strict=True,
        ):
            if expected is None:
                continue
            if delta is None or allowed is None or delta > allowed:
                return None

        return (
            score,
            deltas,
            ("Близкий размер требует подтверждения менеджером",),
            rule.id,
        )


@dataclass(frozen=True, slots=True)
class _GradeMatch:
    match_type: MatchType
    grade_score: float
    matched_grade: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    requires_review: bool
    relation_id: str | None = None


_PRIORITY = {
    MatchType.EXACT: 0,
    MatchType.ALIAS: 1,
    MatchType.CONFIRMED_EQUIVALENT: 2,
    MatchType.PROPOSED_EQUIVALENT: 3,
    MatchType.FUZZY: 4,
    MatchType.NEAR_DIMENSION: 5,
}


def _sort_key(result: SearchResult) -> tuple:
    deltas = tuple(
        Decimal("Infinity") if value is None else value
        for value in result.dimension_deltas
    )
    return (
        _PRIORITY[result.match_type],
        result.requires_review,
        -result.score,
        deltas,
        result.item.supplier.casefold(),
        result.item.id,
    )



def _units_compatible(
    query_units: tuple[str | None, str | None, str | None],
    item_units: tuple[str | None, str | None, str | None],
) -> bool:
    for query_unit, item_unit in zip(query_units, item_units, strict=True):
        if query_unit and item_unit and query_unit.upper() != item_unit.upper():
            return False
    return True


def _mark_ambiguous_fuzzy_ties(results: list[SearchResult]) -> list[SearchResult]:
    buckets: dict[tuple[float, float | None], list[int]] = {}
    for index, result in enumerate(results):
        if result.match_type is not MatchType.FUZZY:
            continue
        key = (
            round(result.grade_score, 6),
            None if result.dimension_score is None else round(result.dimension_score, 6),
        )
        buckets.setdefault(key, []).append(index)

    updated = list(results)
    for indexes in buckets.values():
        if len(indexes) < 2:
            continue
        distinct = {
            normalize_grade_key(results[index].item.grade)
            for index in indexes
        }
        if len(distinct) < 2:
            continue
        warning = (
            "AMBIGUOUS_FUZZY_TIE: несколько обозначений имеют одинаковый "
            "fuzzy-score; оператор должен выбрать вручную"
        )
        for index in indexes:
            current = updated[index]
            updated[index] = replace(
                current,
                warnings=tuple(_dedupe(list(current.warnings) + [warning])),
                requires_review=True,
            )
    return updated

def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
