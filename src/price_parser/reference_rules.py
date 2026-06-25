from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class ReferenceHintStatus(StrEnum):
    CONFIRMED_NTD_ALIAS = "CONFIRMED_NTD_ALIAS"
    PROBABLE_NTD_MATCH = "PROBABLE_NTD_MATCH"
    SOURCE_DESIGNATION_ONLY = "SOURCE_DESIGNATION_ONLY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class ReferenceHint:
    source_designation: str
    suggested_designation: str | None
    status: ReferenceHintStatus
    operator_message: str
    source_reference: str


REFERENCE_HINTS: dict[str, ReferenceHint] = {
    "70С3": ReferenceHint(
        source_designation="70С3",
        suggested_designation="70С3А",
        status=ReferenceHintStatus.PROBABLE_NTD_MATCH,
        operator_message=(
            "Указано поставщиком: 70С3. Возможное нормативное обозначение: "
            "70С3А. Требуется подтверждение оператора; автоматическая замена запрещена."
        ),
        source_reference=(
            "Решение заказчика v0.3.2; первичный НТД для связи 70С3 → 70С3А "
            "требует фиксации"
        ),
    ),
    "42НКД": ReferenceHint(
        source_designation="42НКД",
        suggested_designation=None,
        status=ReferenceHintStatus.SOURCE_DESIGNATION_ONLY,
        operator_message=(
            "Обозначение 42НКД сохранено как указано поставщиком. "
            "Первичный НТД пока не зафиксирован."
        ),
        source_reference="Решение заказчика v0.3.2; первичный НТД не предоставлен",
    ),
    "ЭП847": ReferenceHint(
        source_designation="ЭП847",
        suggested_designation=None,
        status=ReferenceHintStatus.SOURCE_DESIGNATION_ONLY,
        operator_message=(
            "Марка поставщика ЭП847 распознана. Нормативное наименование "
            "не подтверждено первичным НТД; марку не заменять."
        ),
        source_reference="Решение заказчика v0.3.2; первичный НТД не предоставлен",
    ),
}


MESH_NTD_PARAMETERS: dict[str, dict[str, Any]] = {
    "П32": {
        "weave_type": "ПОЛОТНЯНОЕ",
        "warp_count_per_dm": 32,
        "warp_wire_diameter_mm": "0.60",
        "weft_wire_diameter_mm": "0.40",
        "parameter_source": "ГОСТ 3187-76",
        "parameter_origin": "DERIVED_FROM_NTD",
    },
    "П48": {
        "weave_type": "ПОЛОТНЯНОЕ",
        "warp_count_per_dm": 48,
        "warp_wire_diameter_mm": "0.45",
        "weft_wire_diameter_mm": "0.30",
        "parameter_source": "ГОСТ 3187-76",
        "parameter_origin": "DERIVED_FROM_NTD",
    },
}


def reference_hint_for(grade: str) -> ReferenceHint | None:
    return REFERENCE_HINTS.get(grade.strip().upper())


def parse_quantity(value: str) -> tuple[Decimal | None, str | None]:
    match = re.fullmatch(
        r"\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s].*?)?\s*",
        value or "",
        re.I,
    )
    if not match:
        return None, None
    try:
        amount = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None, None
    unit = (match.group(2) or "").strip() or None
    if unit:
        unit = unit.upper().replace("КГ.", "КГ").replace("ШТ.", "ШТ")
    return amount, unit


def inch_fraction(text: str) -> tuple[str, Decimal, Decimal] | None:
    match = re.search(
        r"(?<!\d)([1-9]\d?)\s*/\s*(2|4|8|16|32)(?!\d)\s*(?:\"|дюйм)?",
        text,
        re.I,
    )
    if not match:
        return None
    numerator = Decimal(match.group(1))
    denominator = Decimal(match.group(2))
    value_inch = numerator / denominator
    value_mm = value_inch * Decimal("25.4")
    display = f"{match.group(1)}/{match.group(2)}"
    return display, value_inch, value_mm


def scrap_attributes(description: str) -> dict[str, Any]:
    upper = description.upper().replace("Ё", "Е")
    group_match = re.search(r"\b(?:ГР(?:УППА)?\.?\s*)?(Б\d{1,3})\b", upper)
    if not group_match:
        return {}
    result: dict[str, Any] = {
        "scrap_group": group_match.group(1),
        "classification_source": "ГОСТ 2787-2024",
    }
    material = re.search(r"\b(\d{1,3}[ХX][0-9А-ЯA-Z-]+)\b", upper)
    if material:
        result["source_material_grade"] = material.group(1).replace("X", "Х")
    if re.search(r"\bКУСОК\b", upper):
        result["scrap_form"] = "КУСОК"
    return result


def mesh_attributes(description: str) -> dict[str, Any]:
    upper = description.upper().replace("Ё", "Е")
    designation = re.search(r"\b(П\d{1,3})\b", upper)
    if not designation:
        return {}
    code = designation.group(1)
    result: dict[str, Any] = {"mesh_designation": code}
    width = re.search(r"(?:\bШИР\.?|\bШ\.)\s*(\d+(?:[.,]\d+)?)", upper)
    if width:
        result["width_mm"] = width.group(1).replace(",", ".")
    result.update(MESH_NTD_PARAMETERS.get(code, {}))
    return result


def display_name(
    profile: str,
    grade: str,
    attributes: dict[str, Any],
    dim1_display: str | None = None,
) -> str:
    if profile == "ЛОМ" and attributes.get("scrap_group"):
        value = f"Лом, гр. {attributes['scrap_group']}"
        if attributes.get("scrap_form"):
            value += f", {str(attributes['scrap_form']).lower()}"
        if attributes.get("source_material_grade"):
            value += f", {attributes['source_material_grade']}"
        return value
    if profile == "СЕТКА" and attributes.get("mesh_designation"):
        value = f"Сетка фильтровальная {attributes['mesh_designation']}"
        if attributes.get("width_mm"):
            value += f", ширина {attributes['width_mm']} мм"
        return value
    if dim1_display:
        return f"{profile.title()} {grade} {dim1_display}"
    return f"{profile.title()} {grade}".strip()
