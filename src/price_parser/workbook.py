from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_calamine import CalamineWorkbook


@dataclass(slots=True)
class SheetData:
    name: str
    rows: list[list[Any]]


@dataclass(slots=True)
class WorkbookData:
    path: Path
    sheets: list[SheetData]


def load_workbook(path: str | Path) -> WorkbookData:
    file_path = Path(path)
    if file_path.suffix.lower() not in {".xls", ".xlsx"}:
        raise ValueError(f"Неподдерживаемый формат: {file_path.suffix}")
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    workbook = CalamineWorkbook.from_path(str(file_path))
    try:
        sheets = [
            SheetData(
                name=sheet_name,
                rows=workbook.get_sheet_by_name(sheet_name).to_python(skip_empty_area=False),
            )
            for sheet_name in workbook.sheet_names
        ]
    finally:
        workbook.close()
    return WorkbookData(path=file_path, sheets=sheets)
