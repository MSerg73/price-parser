from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from price_parser.models import ParsedItem

from .errors import InventoryValidationError
from .models import SearchableItem
from .normalization import normalize_profile, parse_dimensions


def load_inventory(path: str | Path) -> list[SearchableItem]:
    file_path = Path(path)
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise InventoryValidationError(f"Файл наличия не найден: {file_path}") from exc

    items: list[SearchableItem] = []
    seen: set[str] = set()
    for line_no, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InventoryValidationError(
                f"Некорректный JSONL, строка {line_no}"
            ) from exc
        if not isinstance(row, dict):
            raise InventoryValidationError(
                f"В строке {line_no} ожидается JSON-объект"
            )

        required = ("id", "supplier", "profile", "grade", "source_reference")
        missing = [field for field in required if not str(row.get(field, "")).strip()]
        if missing:
            raise InventoryValidationError(
                f"Строка {line_no}: отсутствуют поля {', '.join(missing)}"
            )

        item_id = str(row["id"])
        if item_id in seen:
            raise InventoryValidationError(f"Дублирующийся item id: {item_id}")
        seen.add(item_id)

        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            raise InventoryValidationError(
                f"Строка {line_no}: payload должен быть объектом"
            )

        items.append(
            SearchableItem(
                id=item_id,
                supplier=str(row["supplier"]),
                profile=normalize_profile(str(row["profile"])),
                grade=str(row["grade"]).strip(),
                dimensions=parse_dimensions(row.get("dimensions")),
                source_reference=str(row["source_reference"]),
                payload=payload,
                dimension_units=tuple(
                    (row.get("dimension_units") or [None, None, None])[:3]
                ),
            )
        )
    return items


def from_parsed_items(items: Iterable[ParsedItem]) -> list[SearchableItem]:
    result: list[SearchableItem] = []
    for index, item in enumerate(items, start=1):
        result.append(
            SearchableItem(
                id=f"parsed-{index:06d}",
                supplier=item.supplier,
                profile=normalize_profile(item.profile),
                grade=item.grade,
                dimensions=(item.dim1, item.dim2, item.dim3),
                source_reference=item.source.display(),
                dimension_units=(item.dim1_unit, None, None),
                payload={
                    "availability": item.availability,
                    "price_rub_kg": (
                        None
                        if item.price_rub_kg is None
                        else format(item.price_rub_kg, "f")
                    ),
                    "comment": item.comment,
                    "raw_description": item.raw_description,
                    "requires_review": item.requires_review,
                    "display_name": item.display_name,
                    "reference_status": item.reference_status,
                    "reference_research_required": item.reference_research_required,
                    "operator_hints": item.operator_hints,
                    "attributes": item.attributes,
                    "dimension_display": [item.dim1_display, None, None],
                },
            )
        )
    return result
