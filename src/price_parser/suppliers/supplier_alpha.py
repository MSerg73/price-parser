from __future__ import annotations

from ..models import RawItem, SourceRef
from ..normalization import is_service_line, normalize_space
from ..workbook import WorkbookData
from .base import SupplierParser


class SupplierAlphaParser(SupplierParser):
    supplier_name = "SupplierAlpha"

    def matches(self, workbook: WorkbookData) -> bool:
        if workbook.path.name.lower() == "supplier_alpha.xls":
            return True
        if not workbook.sheets:
            return False
        sample = " ".join(
            normalize_space(cell)
            for row in workbook.sheets[0].rows[:8]
            for cell in row
        ).lower()
        return "астор" in sample and sample.count("наименование") >= 2

    def extract(self, workbook: WorkbookData) -> list[RawItem]:
        sheet = workbook.sheets[0]
        result: list[RawItem] = []

        for row_number, row in enumerate(sheet.rows, start=1):
            if row_number <= 5:
                continue
            for block_name, name_col, availability_col, price_col in (
                ("левый блок", 0, 1, 2),
                ("правый блок", 4, 5, 6),
            ):
                description = normalize_space(_cell(row, name_col))
                availability = _cell(row, availability_col)
                price = _cell(row, price_col)

                numeric_values_present = bool(normalize_space(availability) or normalize_space(price))
                if is_service_line(description, numeric_values_present=numeric_values_present):
                    continue
                if not description:
                    continue

                # Product rows generally have stock and/or price. Rows without either are section labels.
                if not normalize_space(availability) and not normalize_space(price):
                    continue

                result.append(
                    RawItem(
                        supplier=self.supplier_name,
                        description=description,
                        availability=availability,
                        price=price,
                        source=SourceRef(
                            file=workbook.path.name,
                            sheet=sheet.name,
                            row=row_number,
                            block=block_name,
                        ),
                        extra={"nds_unknown": True},
                    )
                )
        return result


def _cell(row: list[object], index: int) -> object:
    return row[index] if index < len(row) else ""
