from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ..models import RawItem, SourceRef
from ..normalization import is_service_line, normalize_space
from ..workbook import WorkbookData
from .base import SupplierParser


HEADER_KEYWORDS = {
    "description": ("наименование", "номенклатура", "товар", "продукция", "позиция", "названия"),
    "grade": ("марка", "сплав", "материал"),
    "profile": ("вид проката", "профиль", "форма проката", "тип проката"),
    "dim_primary": ("диаметр", "диамер", "толщина", "толшина", "размер 1"),
    "dimensions": ("размеры мм", "размеры", "габарит", "длина", "раскрой", "размер"),
    "availability": ("наличие", "в наличии", "остаток", "вес", "количество", "кол-во", "свободный"),
    "price": ("цена", "стоимость", "руб/кг", "руб./кг"),
}

PRICE_WITH_VAT_MARKERS = (
    "с ндс",
    "включая ндс",
    "ндс включ",
    "с учетом ндс",
    "с учётом ндс",
)
PRICE_WITHOUT_VAT_MARKERS = ("без ндс",)


class GenericParser(SupplierParser):
    """Fallback for a previously unseen single-table price list.

    It does not guess semantics silently: unresolved fields stay empty and are
    sent to the LLM candidate list. The parser preserves source coordinates.
    """

    supplier_name = "Неизвестный поставщик"

    def matches(self, workbook: WorkbookData) -> bool:
        return True

    def extract(self, workbook: WorkbookData) -> list[RawItem]:
        supplier = _supplier_from_filename(workbook.path)
        result: list[RawItem] = []
        workbook_mapping: dict[str, int] | None = None
        workbook_headers: list[Any] | None = None
        workbook_availability_kg = False

        for sheet in workbook.sheets:
            header_index, mapping = _detect_header(sheet.rows)
            if mapping.get("description") is not None:
                if header_index > 0 and workbook_mapping is not None:
                    # Parse the headerless prefix using the last explicit schema
                    # from this workbook, then switch to the newly detected schema.
                    result.extend(
                        _extract_mapped_rows(
                            workbook.path.name,
                            sheet.name,
                            sheet.rows[:header_index],
                            supplier,
                            -1,
                            workbook_mapping,
                            header_row=workbook_headers,
                            availability_unit_kg=workbook_availability_kg,
                        )
                    )
                workbook_mapping = dict(mapping)
                workbook_headers = list(sheet.rows[header_index])
                workbook_availability_kg = _availability_header_uses_kg(
                    sheet.rows[header_index],
                    mapping.get("availability"),
                )
                result.extend(
                    _extract_mapped_rows(
                        workbook.path.name,
                        sheet.name,
                        sheet.rows,
                        supplier,
                        header_index,
                        mapping,
                        header_row=workbook_headers,
                        availability_unit_kg=workbook_availability_kg,
                    )
                )
            elif workbook_mapping is not None:
                # Some supplier workbooks use the same columns on secondary
                # sheets but omit the header row. Reuse only a mapping already
                # proved by an explicit header in the same workbook.
                result.extend(
                    _extract_mapped_rows(
                        workbook.path.name,
                        sheet.name,
                        sheet.rows,
                        supplier,
                        -1,
                        workbook_mapping,
                        header_row=workbook_headers,
                        availability_unit_kg=workbook_availability_kg,
                    )
                )
            else:
                result.extend(
                    _extract_text_rows(
                        workbook.path.name,
                        sheet.name,
                        sheet.rows,
                        supplier,
                    )
                )
        return result


def _detect_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    best_score = 0
    best_row = -1
    best_mapping: dict[str, int] = {}

    for row_index, row in enumerate(rows[:500]):
        mapping: dict[str, int] = {}
        score = 0
        for column_index, value in enumerate(row):
            text = normalize_space(value).lower()
            if not text:
                continue
            price_field = _price_header_field(text)
            if price_field and price_field not in mapping:
                mapping[price_field] = column_index
                score += 1
                continue

            for field, keywords in HEADER_KEYWORDS.items():
                if field in mapping:
                    continue
                if field == "price" and price_field:
                    continue
                if any(keyword in text for keyword in keywords):
                    mapping[field] = column_index
                    score += 2 if field == "description" else 1
                    break
        if score > best_score:
            best_score = score
            best_row = row_index
            best_mapping = mapping

    if best_score < 2 or "description" not in best_mapping:
        return -1, {}
    return best_row, best_mapping


def _extract_mapped_rows(
    file_name: str,
    sheet_name: str,
    rows: list[list[Any]],
    supplier: str,
    header_index: int,
    mapping: dict[str, int],
    *,
    header_row: list[Any] | None,
    availability_unit_kg: bool,
) -> list[RawItem]:
    result: list[RawItem] = []
    start = header_index + 1
    last_source_name = ""

    for zero_row, row in enumerate(rows[start:], start=start):
        source_name_raw = normalize_space(_cell(row, mapping["description"]))
        profile_hint = normalize_space(_cell(row, mapping.get("profile")))
        dim_primary = normalize_space(_cell(row, mapping.get("dim_primary")))
        dimensions = normalize_space(_cell(row, mapping.get("dimensions")))
        availability = _cell(row, mapping.get("availability"))
        price, price_comment = _select_price(row, mapping)
        schema_shift_comment: str | None = None

        if _looks_like_dimension_value(profile_hint):
            # A later table block can shift the size into the nominal profile
            # column. Do not publish a size range as a product profile.
            shifted_size = profile_hint
            inferred_profile = _infer_profile_from_text(source_name_raw)
            profile_hint = inferred_profile
            if not _looks_like_dimension_value(dim_primary):
                minimum_order = dim_primary
                dim_primary = shifted_size
                if minimum_order:
                    schema_shift_comment = (
                        "Обнаружено смещение колонок; значение "
                        f"«{minimum_order}» сохранено как дополнительное условие"
                    )
            else:
                dim_primary = shifted_size
            if not normalize_space(availability):
                shifted_availability = normalize_space(_cell(row, 4))
                if shifted_availability:
                    availability = shifted_availability
            dimensions = (
                dimensions
                if _looks_like_size_expression(dimensions)
                else ""
            )

        has_product_evidence = bool(
            profile_hint
            or dim_primary
            or dimensions
            or normalize_space(availability)
            or normalize_space(price)
        )
        if source_name_raw:
            last_source_name = source_name_raw
        source_name = source_name_raw or (last_source_name if has_product_evidence else "")
        grade_hint = normalize_space(_cell(row, mapping.get("grade"))) or None

        if (
            not dim_primary
            and not dimensions
            and not normalize_space(availability)
            and not normalize_space(price)
            and _looks_like_service_metadata(source_name, profile_hint)
        ):
            continue

        description = _compose_description(
            source_name=source_name,
            profile_hint=profile_hint,
            dim_primary=dim_primary,
            dimensions=dimensions,
        )
        numeric_present = bool(
            normalize_space(availability)
            or normalize_space(price)
            or dim_primary
            or dimensions
        )
        if is_service_line(description, numeric_values_present=numeric_present):
            continue
        if not source_name:
            continue

        comments = ["Структура определена generic-парсером; требуется контроль"]
        if price_comment:
            comments.append(price_comment)
        if schema_shift_comment:
            comments.append(schema_shift_comment)
        if profile_hint:
            comments.append(f"Исходный вид проката: {profile_hint}")
        if dim_primary or dimensions:
            comments.append(
                "Размеры взяты из отдельных колонок поставщика"
            )
        outer_designation = _outer_designation(source_name)
        if outer_designation:
            comments.append(
                f"Исходное обозначение поставщика: {outer_designation}"
            )

        result.append(
            RawItem(
                supplier=supplier,
                description=description,
                availability=availability,
                price=price,
                grade_hint=grade_hint,
                source=SourceRef(
                    file=file_name,
                    sheet=sheet_name,
                    row=zero_row + 1,
                ),
                extra={
                    "comments": comments,
                    "profile_hint": profile_hint or None,
                    "structured_profile_column": mapping.get("profile") is not None,
                    "structured_dimensions": {
                        "primary": dim_primary or None,
                        "secondary": dimensions or None,
                    },
                    "source_columns": {
                        "description": source_name or None,
                        "profile": profile_hint or None,
                        "dim_primary": dim_primary or None,
                        "dimensions": dimensions or None,
                        "availability": (
                            normalize_space(availability) or None
                        ),
                        **_source_snapshot(row, header_row),
                    },
                    "availability_unit": "кг" if availability_unit_kg else None,
                    "generic_parser": True,
                },
            )
        )
    return result


def _source_snapshot(
    row: list[Any],
    header_row: list[Any] | None,
) -> dict[str, Any]:
    """Preserve every source cell instead of truncating the row to A-I."""

    raw_row = [normalize_space(value) or None for value in row]
    raw_headers = (
        [normalize_space(value) or None for value in header_row]
        if header_row is not None
        else []
    )

    by_header: dict[str, Any] = {}
    for index, value in enumerate(raw_row):
        if value is None:
            continue
        header = (
            raw_headers[index]
            if index < len(raw_headers) and raw_headers[index]
            else f"COL_{index + 1}"
        )
        key = str(header)
        if key in by_header:
            key = f"{key} [{index + 1}]"
        by_header[key] = value

    return {
        "raw_row": raw_row,
        "raw_headers": raw_headers,
        "by_header": by_header,
    }


def _compose_description(
    *,
    source_name: str,
    profile_hint: str,
    dim_primary: str,
    dimensions: str,
) -> str:
    parts = [
        value
        for value in (profile_hint, source_name, dim_primary, dimensions)
        if value
    ]
    return normalize_space(" ".join(parts))





def _looks_like_service_metadata(source_name: str, profile_hint: str) -> bool:
    text = normalize_space(f"{source_name} {profile_hint}").lower()
    markers = (
        "срок поставки",
        "минимальн",
        "по всем вопросам",
        "по все вапросам",
        "следующие марки",
        "обращайтес",
        "обрашайтес",
    )
    return any(marker in text for marker in markers)


def _looks_like_dimension_value(value: str) -> bool:
    text = normalize_space(value).lower()
    if not text:
        return False
    return bool(
        re.match(
            r"^(?:(?:ф|ø|⌀)\s*)?(?:от\s+)?\d+(?:[.,]\d+)?"
            r"(?:\s*[-–—хx×]\s*\d+(?:[.,]\d+)?)?",
            text,
        )
    )


def _looks_like_size_expression(value: str) -> bool:
    text = normalize_space(value).lower()
    if not text:
        return False
    if any(marker in text for marker in ("гост", "ту ", "ост ")):
        return False
    return bool(re.search(r"\d", text)) and (
        "мм" in text
        or bool(re.search(r"\d\s*[хx×]\s*\d", text))
        or bool(re.fullmatch(r"\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?", text))
    )


def _infer_profile_from_text(value: str) -> str:
    text = normalize_space(value).lower()
    patterns = (
        (r"\b(?:пруток|прутки)\b", "ПРУТОК"),
        (r"\b(?:круг|круги|круглый)\b", "КРУГ"),
        (r"\b(?:труба|трубка|трубки)\b", "ТРУБА"),
        (r"\b(?:проволока|проволоки)\b", "ПРОВОЛОКА"),
        (r"\b(?:лист|листы)\b", "ЛИСТ"),
        (r"\b(?:плита|плиты)\b", "ПЛИТА"),
        (r"\b(?:полоса|полосы)\b", "ПОЛОСА"),
        (r"\b(?:лента|ленты)\b", "ЛЕНТА"),
        (r"\b(?:квадрат|квадраты)\b", "КВАДРАТ"),
        (r"\b(?:шестигранник|шестигранники)\b", "ШЕСТИГРАННИК"),
        (r"\b(?:кольцо|кольца)\b", "КОЛЬЦО"),
        (r"\b(?:поковка|поковки)\b", "ПОКОВКА"),
    )
    for pattern, profile in patterns:
        if re.search(pattern, text, re.I):
            return profile
    return ""


def _outer_designation(value: str) -> str | None:
    match = re.match(
        r"^\s*([A-Za-zА-Яа-яЁё]+\s*[-]?\s*\d+[A-Za-zА-Яа-яЁё0-9-]*)\s*\(",
        value,
    )
    return normalize_space(match.group(1)) if match else None

def _availability_header_uses_kg(
    header_row: list[Any],
    index: int | None,
) -> bool:
    if index is None:
        return False
    header = normalize_space(_cell(header_row, index)).lower()
    return "кг" in header

def _extract_text_rows(
    file_name: str,
    sheet_name: str,
    rows: list[list[Any]],
    supplier: str,
) -> list[RawItem]:
    result: list[RawItem] = []

    for row_number, row in enumerate(rows, start=1):
        text_cells = [
            (index, normalize_space(value))
            for index, value in enumerate(row)
            if normalize_space(value) and not _looks_numeric(value)
        ]
        if not text_cells:
            continue

        # Longest text cell is the safest description candidate.
        description_col, description = max(text_cells, key=lambda pair: len(pair[1]))
        numeric_values = [
            value for value in row if _looks_numeric(value) or _has_numeric_unit(value)
        ]
        if is_service_line(description, numeric_values_present=bool(numeric_values)):
            continue
        if len(description) < 4:
            continue

        availability = numeric_values[0] if numeric_values else ""
        price = numeric_values[-1] if len(numeric_values) >= 2 else ""
        result.append(
            RawItem(
                supplier=supplier,
                description=description,
                availability=availability,
                price=price,
                source=SourceRef(
                    file=file_name,
                    sheet=sheet_name,
                    row=row_number,
                ),
                extra={
                    "comments": [
                        "Колонки не распознаны; строка подготовлена для LLM-проверки"
                    ],
                    "source_columns": _source_snapshot(row, None),
                },
            )
        )
    return result



def _price_header_field(text: str) -> str | None:
    if not any(keyword in text for keyword in HEADER_KEYWORDS["price"]):
        return None
    if any(marker in text for marker in PRICE_WITHOUT_VAT_MARKERS):
        return "price_without_vat"
    if any(marker in text for marker in PRICE_WITH_VAT_MARKERS):
        return "price_with_vat"
    return None


def _select_price(
    row: list[Any],
    mapping: dict[str, int],
) -> tuple[Any, str | None]:
    with_vat = _cell(row, mapping.get("price_with_vat"))
    generic = _cell(row, mapping.get("price"))
    without_vat = _cell(row, mapping.get("price_without_vat"))

    if normalize_space(with_vat):
        comment = (
            "Из двух цен выбрана цена с НДС"
            if normalize_space(without_vat)
            else "Цена указана с НДС"
        )
        return with_vat, comment
    if normalize_space(generic):
        return generic, "Статус НДС в заголовке цены не определён"
    if normalize_space(without_vat):
        return without_vat, "Доступна только цена без НДС"
    return "", None


def _cell(row: list[Any], index: int | None) -> Any:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _looks_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_numeric_unit(value: Any) -> bool:
    text = normalize_space(value).lower()
    return any(unit in text for unit in (" кг", " шт", " м", " руб")) and any(
        character.isdigit() for character in text
    )


def _supplier_from_filename(path: Path) -> str:
    name = path.stem.replace("_", " ").strip()
    return name or "Неизвестный поставщик"
