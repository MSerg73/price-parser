from __future__ import annotations

from datetime import date, datetime

from ..models import RawItem, SourceRef
from ..normalization import normalize_space
from ..workbook import WorkbookData
from .base import SupplierParser


class SupplierGammaParser(SupplierParser):
    supplier_name = "Supplier Gamma"

    def matches(self, workbook: WorkbookData) -> bool:
        if "supplier_gamma" in workbook.path.name.lower():
            return True
        if not workbook.sheets:
            return False
        sample = " ".join(
            normalize_space(cell)
            for row in workbook.sheets[0].rows[:10]
            for cell in row
        ).lower()
        return "supplier gamma" in sample and "сплав" in sample

    def extract(self, workbook: WorkbookData) -> list[RawItem]:
        # Лист3 is a reduced duplicate without availability; use the full main sheet only.
        sheet = workbook.sheets[0]
        result: list[RawItem] = []

        for row_number, row in enumerate(sheet.rows, start=1):
            warehouse = normalize_space(_cell(row, 0))
            grade = normalize_space(_cell(row, 1))
            description = normalize_space(_cell(row, 2))
            stock_kg = _cell(row, 3)
            stock_units = normalize_space(_cell(row, 4))
            production_kg = _cell(row, 5)
            delivery_date = _cell(row, 6)

            if row_number <= 8 or not description:
                continue
            if description.strip().upper() in {"ИТОГО", "ВСЕГО"}:
                continue
            if not grade and not _is_number(stock_kg) and not _is_number(production_kg):
                continue

            availability = ""
            comments: list[str] = []
            if _is_number(stock_kg):
                availability = f"{_number_text(stock_kg)} кг"
            if stock_units:
                comments.append(f"Дополнительное количество: {stock_units}")
            if _is_number(production_kg) and float(production_kg) != 0:
                production_text = f"{_number_text(production_kg)} кг"
                if availability and float(stock_kg or 0) != 0:
                    comments.append(f"В производстве: {production_text}")
                else:
                    availability = f"В производстве: {production_text}"
            if delivery_date:
                comments.append(f"Срок поставки: {_date_text(delivery_date)}")
            if warehouse:
                comments.append(f"Склад: {warehouse}")

            result.append(
                RawItem(
                    supplier=self.supplier_name,
                    description=description,
                    availability=availability,
                    price=None,
                    grade_hint=grade or None,
                    source=SourceRef(
                        file=workbook.path.name,
                        sheet=sheet.name,
                        row=row_number,
                    ),
                    extra={"comments": comments},
                )
            )

        return result


def _cell(row: list[object], index: int) -> object:
    return row[index] if index < len(row) else ""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_text(value: object) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _date_text(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    return normalize_space(value)
