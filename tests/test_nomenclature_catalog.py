from __future__ import annotations

import json
from pathlib import Path

import pytest

from price_parser.nomenclature.catalog import Catalog, load_catalog
from price_parser.nomenclature.errors import (
    CatalogValidationError,
    InventoryValidationError,
)
from price_parser.nomenclature.models import (
    AliasDefinition,
    GradeDefinition,
    RecordStatus,
)
from price_parser.nomenclature.repository import load_inventory


def test_default_catalog_loads_and_has_version() -> None:
    catalog = load_catalog()
    assert catalog.version == "1.1.0"
    assert len(catalog.grades) >= 70


def test_confirmed_alias_has_source() -> None:
    catalog = load_catalog()
    alias = next(record for record in catalog.aliases if record.alias == "C17200")
    assert alias.status is RecordStatus.CONFIRMED
    assert alias.source_reference


def test_conflicting_alias_is_rejected() -> None:
    grades = (
        GradeDefinition("g1", "М1", RecordStatus.CONFIRMED, "source"),
        GradeDefinition("g2", "М2", RecordStatus.CONFIRMED, "source"),
    )
    aliases = (
        AliasDefinition("a1", "TEST", "g1", RecordStatus.CONFIRMED, "source"),
        AliasDefinition("a2", "TEST", "g2", RecordStatus.CONFIRMED, "source"),
    )
    with pytest.raises(CatalogValidationError, match="несколькими марками"):
        Catalog("test", grades, aliases, (), ())


def test_confirmed_record_without_source_is_rejected() -> None:
    with pytest.raises(CatalogValidationError, match="источник"):
        Catalog(
            "test",
            (GradeDefinition("g1", "М1", RecordStatus.CONFIRMED, ""),),
            (),
            (),
            (),
        )


def test_inventory_duplicate_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "inventory.jsonl"
    row = {
        "id": "same",
        "supplier": "test",
        "profile": "ТРУБА",
        "grade": "М1",
        "dimensions": ["5", "1"],
        "source_reference": "fixture",
    }
    path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n"
        + json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(InventoryValidationError, match="Дублирующийся"):
        load_inventory(path)


def test_broken_inventory_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "inventory.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(InventoryValidationError, match="строка 1"):
        load_inventory(path)
