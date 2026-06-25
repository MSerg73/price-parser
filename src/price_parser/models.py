from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class SourceRef:
    file: str
    sheet: str
    row: int
    block: str | None = None

    def display(self) -> str:
        value = f"{self.file} / {self.sheet} / строка {self.row}"
        if self.block:
            value += f" / {self.block}"
        return value


@dataclass(slots=True)
class RawItem:
    supplier: str
    description: str
    availability: Any
    price: Any
    source: SourceRef
    grade_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedItem:
    supplier: str
    profile: str
    grade: str
    dim1: Decimal | None
    dim2: Decimal | None
    dim3: Decimal | None
    availability: str
    price_rub_kg: Decimal | None
    comment: str
    source: SourceRef
    raw_description: str
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    domain: str = "METAL_PRODUCT"
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    display_name: str | None = None
    quantity_value: Decimal | None = None
    quantity_unit: str | None = None
    dim1_display: str | None = None
    dim2_display: str | None = None
    dim3_display: str | None = None
    dim1_unit: str | None = None
    dim1_role: str | None = None
    reference_dim1_mm: Decimal | None = None
    reference_status: str | None = None
    reference_research_required: bool = False
    operator_hints: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def output_row(self) -> list[Any]:
        return [
            self.supplier,
            self.profile,
            self.display_name or "",
            self.grade,
            _decimal_to_cell(self.dim1),
            self.dim1_display or "",
            self.dim1_unit or "",
            self.dim2_display or _decimal_to_cell(self.dim2),
            self.dim3_display or _decimal_to_cell(self.dim3),
            self.availability,
            _decimal_to_cell(self.quantity_value),
            self.quantity_unit or "",
            "?" if self.price_rub_kg is None else _decimal_to_cell(self.price_rub_kg),
            self.reference_status or "",
            " | ".join(self.operator_hints),
            self.comment,
            self.source.display(),
        ]


@dataclass(slots=True)
class ParseStats:
    input_files: int = 0
    raw_items: int = 0
    parsed_items: int = 0
    skipped_rows: int = 0
    warnings: int = 0
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0


def _decimal_to_cell(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)
