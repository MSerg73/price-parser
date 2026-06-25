from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Iterable

from .errors import CatalogValidationError
from .models import (
    AliasDefinition,
    DimensionRule,
    EquivalenceDefinition,
    GradeDefinition,
    GradeResolution,
    RecordStatus,
    RelationType,
)
from .normalization import normalize_grade_key, normalize_profile


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    grades: tuple[GradeDefinition, ...]
    aliases: tuple[AliasDefinition, ...]
    equivalences: tuple[EquivalenceDefinition, ...]
    dimension_rules: tuple[DimensionRule, ...]
    grade_by_id: dict[str, GradeDefinition] = field(init=False, repr=False, compare=False)
    grade_by_key: dict[str, GradeDefinition] = field(init=False, repr=False, compare=False)
    alias_by_key: dict[str, AliasDefinition] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_catalog(self)
        object.__setattr__(
            self,
            "grade_by_id",
            {record.id: record for record in self.grades},
        )
        object.__setattr__(
            self,
            "grade_by_key",
            {
                normalize_grade_key(record.canonical): record
                for record in self.grades
            },
        )
        object.__setattr__(
            self,
            "alias_by_key",
            {
                normalize_grade_key(record.alias): record
                for record in self.aliases
                if record.status is not RecordStatus.REJECTED
            },
        )

    def resolve_grade(self, raw: str) -> GradeResolution:
        key = normalize_grade_key(raw)
        direct = self.grade_by_key.get(key)
        if direct:
            return GradeResolution(
                raw=raw,
                canonical_grade_id=direct.id,
                canonical=direct.canonical,
                resolution_type="CANONICAL",
                status=direct.status,
                source_reference=direct.source_reference,
            )

        alias = self.alias_by_key.get(key)
        if alias:
            canonical = self.grade_by_id[alias.canonical_grade_id]
            return GradeResolution(
                raw=raw,
                canonical_grade_id=canonical.id,
                canonical=canonical.canonical,
                resolution_type="ALIAS",
                status=alias.status,
                source_reference=alias.source_reference,
            )

        return GradeResolution(
            raw=raw,
            canonical_grade_id=None,
            canonical=None,
            resolution_type="UNKNOWN",
            status=None,
            source_reference=None,
        )

    def match_values_for_grade_id(self, grade_id: str) -> tuple[str, ...]:
        grade = self.grade_by_id.get(grade_id)
        if grade is None:
            return ()
        values = [grade.canonical]
        values.extend(
            alias.alias
            for alias in self.aliases
            if alias.canonical_grade_id == grade_id
            and alias.status is not RecordStatus.REJECTED
        )
        return tuple(values)

    def relation(
        self,
        source_grade_id: str,
        target_grade_id: str,
    ) -> EquivalenceDefinition | None:
        for relation in self.equivalences:
            if relation.status is RecordStatus.REJECTED:
                continue
            direct = (
                relation.source_grade_id == source_grade_id
                and relation.target_grade_id == target_grade_id
            )
            reverse = (
                relation.bidirectional
                and relation.source_grade_id == target_grade_id
                and relation.target_grade_id == source_grade_id
            )
            if direct or reverse:
                return relation
        return None

    def dimension_rule(self, profile: str) -> DimensionRule | None:
        normalized = normalize_profile(profile)
        candidates = [
            rule
            for rule in self.dimension_rules
            if normalize_profile(rule.profile) == normalized
            and rule.status is RecordStatus.CONFIRMED
        ]
        if not candidates:
            return None
        return candidates[0]


def load_catalog(catalog_dir: str | Path | None = None) -> Catalog:
    root = Path(catalog_dir) if catalog_dir else _default_catalog_dir()
    metadata = _load_json(root / "catalog.json")
    grades = tuple(
        GradeDefinition(
            id=row["id"],
            canonical=row["canonical"],
            status=RecordStatus(row["status"]),
            source_reference=row["source_reference"],
            standard_system=row.get("standard_system"),
        )
        for row in _load_jsonl(root / "grades.jsonl")
    )
    aliases = tuple(
        AliasDefinition(
            id=row["id"],
            alias=row["alias"],
            canonical_grade_id=row["canonical_grade_id"],
            status=RecordStatus(row["status"]),
            source_reference=row["source_reference"],
        )
        for row in _load_jsonl(root / "aliases.jsonl")
    )
    equivalences = tuple(
        EquivalenceDefinition(
            id=row["id"],
            source_grade_id=row["source_grade_id"],
            target_grade_id=row["target_grade_id"],
            relation_type=RelationType(row["relation_type"]),
            status=RecordStatus(row["status"]),
            source_reference=row["source_reference"],
            bidirectional=bool(row.get("bidirectional", True)),
        )
        for row in _load_jsonl(root / "equivalences.jsonl")
    )

    dimension_payload = _load_json(root / "dimension_rules.json")
    rules = tuple(
        DimensionRule(
            id=row["id"],
            profile=row["profile"],
            max_absolute_delta=tuple(
                None if value is None else Decimal(str(value))
                for value in row["max_absolute_delta"]
            ),
            status=RecordStatus(row["status"]),
            source_reference=row["source_reference"],
        )
        for row in dimension_payload.get("rules", [])
    )

    return Catalog(
        version=str(metadata["version"]),
        grades=grades,
        aliases=aliases,
        equivalences=equivalences,
        dimension_rules=rules,
    )


def _default_catalog_dir() -> Path:
    return Path(str(files("price_parser").joinpath("data", "nomenclature")))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogValidationError(f"Файл справочника не найден: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(
            f"Некорректный JSON в {path.name}: строка {exc.lineno}"
        ) from exc


def _load_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise CatalogValidationError(f"Файл справочника не найден: {path}") from exc

    rows: list[dict] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CatalogValidationError(
                f"Некорректный JSONL в {path.name}, строка {line_no}"
            ) from exc
        if not isinstance(value, dict):
            raise CatalogValidationError(
                f"Ожидался объект в {path.name}, строка {line_no}"
            )
        rows.append(value)
    return rows


def _validate_catalog(catalog: Catalog) -> None:
    if not catalog.version.strip():
        raise CatalogValidationError("Версия справочника не указана")

    _ensure_unique((record.id for record in catalog.grades), "ID марки")
    _ensure_unique((record.id for record in catalog.aliases), "ID псевдонима")
    _ensure_unique((record.id for record in catalog.equivalences), "ID связи")
    _ensure_unique((record.id for record in catalog.dimension_rules), "ID размерного правила")

    grade_by_id = {record.id: record for record in catalog.grades}
    canonical_keys: dict[str, str] = {}
    for grade in catalog.grades:
        _validate_source(grade.status, grade.source_reference, f"марка {grade.id}")
        key = normalize_grade_key(grade.canonical)
        previous = canonical_keys.get(key)
        if previous:
            raise CatalogValidationError(
                f"Дублирующая каноническая марка: {grade.canonical!r} ({previous}, {grade.id})"
            )
        canonical_keys[key] = grade.id

    alias_keys: dict[str, str] = {}
    for alias in catalog.aliases:
        if alias.canonical_grade_id not in grade_by_id:
            raise CatalogValidationError(
                f"Псевдоним {alias.id} ссылается на неизвестную марку "
                f"{alias.canonical_grade_id}"
            )
        _validate_source(alias.status, alias.source_reference, f"псевдоним {alias.id}")
        key = normalize_grade_key(alias.alias)
        if key in canonical_keys and canonical_keys[key] != alias.canonical_grade_id:
            raise CatalogValidationError(
                f"Псевдоним {alias.alias!r} конфликтует с канонической маркой"
            )
        previous = alias_keys.get(key)
        if previous and previous != alias.canonical_grade_id:
            raise CatalogValidationError(
                f"Псевдоним {alias.alias!r} связан с несколькими марками"
            )
        alias_keys[key] = alias.canonical_grade_id

    for relation in catalog.equivalences:
        if relation.source_grade_id not in grade_by_id:
            raise CatalogValidationError(
                f"Связь {relation.id}: неизвестная исходная марка"
            )
        if relation.target_grade_id not in grade_by_id:
            raise CatalogValidationError(
                f"Связь {relation.id}: неизвестная целевая марка"
            )
        if relation.source_grade_id == relation.target_grade_id:
            raise CatalogValidationError(f"Связь {relation.id} ведёт на ту же марку")
        _validate_source(
            relation.status,
            relation.source_reference,
            f"связь {relation.id}",
        )

    confirmed_profiles: set[str] = set()
    for rule in catalog.dimension_rules:
        _validate_source(rule.status, rule.source_reference, f"правило {rule.id}")
        if len(rule.max_absolute_delta) != 3:
            raise CatalogValidationError(
                f"Правило {rule.id}: требуется три компонента допуска"
            )
        if any(value is not None and value < 0 for value in rule.max_absolute_delta):
            raise CatalogValidationError(f"Правило {rule.id}: отрицательный допуск")
        if rule.status is RecordStatus.CONFIRMED:
            profile = normalize_profile(rule.profile)
            if profile in confirmed_profiles:
                raise CatalogValidationError(
                    f"Для профиля {profile} задано несколько подтверждённых правил"
                )
            confirmed_profiles.add(profile)


def _ensure_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if not value or not value.strip():
            raise CatalogValidationError(f"{label} не может быть пустым")
        if value in seen:
            raise CatalogValidationError(f"Дублирующийся {label}: {value}")
        seen.add(value)


def _validate_source(status: RecordStatus, source: str, context: str) -> None:
    if status is RecordStatus.CONFIRMED and not source.strip():
        raise CatalogValidationError(
            f"Подтверждённый объект должен иметь источник: {context}"
        )
