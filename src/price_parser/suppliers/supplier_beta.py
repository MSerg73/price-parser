from __future__ import annotations

from ..models import RawItem, SourceRef
from ..normalization import is_service_line, normalize_space
from ..workbook import WorkbookData
from .base import SupplierParser


class SupplierBetaParser(SupplierParser):
    supplier_name = "SupplierBeta"

    def matches(self, workbook: WorkbookData) -> bool:
        if workbook.path.name.lower() == "supplier_beta.xls":
            return True
        if not workbook.sheets:
            return False
        sample = " ".join(
            normalize_space(cell)
            for row in workbook.sheets[0].rows[:8]
            for cell in row
        ).lower()
        return "остатки и резервы товаров" in sample and "свободный остаток" in sample

    def extract(self, workbook: WorkbookData) -> list[RawItem]:
        sheet = workbook.sheets[0]
        result: list[RawItem] = []

        for row_number, row in enumerate(sheet.rows, start=1):
            description = normalize_space(_cell(row, 1))
            stock = _cell(row, 2)
            reserved = _cell(row, 3)
            free_stock = _cell(row, 4)

            numeric_values_present = any(_is_number(value) for value in (stock, reserved, free_stock))
            if is_service_line(description, numeric_values_present=numeric_values_present):
                continue
            if not description or not numeric_values_present:
                continue

            comments: list[str] = []
            if _is_number(stock):
                comments.append(f"Остаток: {_number_text(stock)} кг")
            if _is_number(reserved) and float(reserved) != 0:
                comments.append(f"Зарезервировано: {_number_text(reserved)} кг")

            result.append(
                RawItem(
                    supplier=self.supplier_name,
                    description=description,
                    availability=free_stock,
                    price=None,
                    source=SourceRef(
                        file=workbook.path.name,
                        sheet=sheet.name,
                        row=row_number,
                    ),
                    extra={
                        "availability_unit": "кг",
                        "comments": comments,
                    },
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
