from __future__ import annotations

import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .models import ParsedItem
from .search import SearchResult

HEADERS = [
    "Поставщик",
    "Профиль",
    "Наименование оператора",
    "Марка/код",
    "Размер 1",
    "Размер 1 (исходный вид)",
    "Единица размера 1",
    "Размер 2",
    "Размер 3",
    "Наличие",
    "Количество",
    "Единица количества",
    "Цена",
    "Статус НТД",
    "Подсказка оператору",
    "Комментарий",
    "Источник (файл/строка)",
]


ASSIGNMENT_HEADERS = [
    "Поставщик",
    "Профиль",
    "Марка",
    "Размер 1",
    "Размер 2",
    "Размер 3",
    "Наличие",
    "Цена (₽/кг)",
    "Комментарий",
    "Источник (файл/строка)",
]

ASSIGNMENT_SEARCH_HEADERS = [
    "Тип совпадения",
    "Отклонение",
    *ASSIGNMENT_HEADERS,
]

def export_xlsx(
    path: str | Path,
    items: list[ParsedItem],
    search_results: list[SearchResult] | None = None,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    sheets: list[tuple[str, list[list[Any]]]] = [
        ("Результат", [HEADERS] + [item.output_row() for item in items])
    ]
    if search_results is not None:
        search_headers = ["Тип совпадения", "Отклонение"] + HEADERS
        search_rows = [
            [result.match_type, _number(result.size_delta)] + result.item.output_row()
            for result in search_results
        ]
        sheets.append(("Поиск БрБ2 ф20", [search_headers] + search_rows))

    _write_workbook(output, sheets)



def assignment_output_row(
    item: ParsedItem,
    *,
    profile_override: str | None = None,
    comment_override: str | None = None,
) -> list[Any]:
    """Return exactly the ten columns required by the candidate test assignment."""
    return [
        item.supplier,
        profile_override or item.profile,
        item.grade,
        _dimension_cell(item.dim1_display, item.dim1),
        _dimension_cell(item.dim2_display, item.dim2),
        _dimension_cell(item.dim3_display, item.dim3),
        item.availability,
        "?" if item.price_rub_kg is None else _number(item.price_rub_kg),
        item.comment if comment_override is None else comment_override,
        item.source.display(),
    ]


def export_assignment_xlsx(
    path: str | Path,
    items: list[ParsedItem],
    search_results: list[SearchResult] | None = None,
) -> None:
    """Create the submission workbook with one exact ten-column result sheet.

    ``search_results`` is accepted for backwards compatibility but is not
    embedded into the submission workbook. Search evidence must be exported
    separately with :func:`export_assignment_search_xlsx`.
    """
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    sheets: list[tuple[str, list[list[Any]]]] = [
        (
            "Результат",
            [ASSIGNMENT_HEADERS]
            + [assignment_output_row(item) for item in items],
        )
    ]
    _write_workbook(output, sheets, assignment_layout=True)



def export_assignment_rows_xlsx(
    path: str | Path,
    rows: list[list[Any]],
    *,
    sheet_name: str = "Результат",
    default_filters: dict[int, Any] | None = None,
) -> None:
    """Export prebuilt ten-column assignment rows into one XLSX sheet."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows, start=1):
        if len(row) != len(ASSIGNMENT_HEADERS):
            raise ValueError(
                f"Строка {index} содержит {len(row)} колонок вместо "
                f"{len(ASSIGNMENT_HEADERS)}"
            )
    _write_workbook(
        output,
        [(_safe_sheet_name(sheet_name), [ASSIGNMENT_HEADERS] + rows)],
        assignment_layout=True,
        default_filters=default_filters,
    )


def export_assignment_search_xlsx(
    path: str | Path,
    search_results: list[SearchResult],
    *,
    default_diameter: Decimal | None = Decimal("20"),
) -> None:
    """Create a separate, auditable search-view workbook.

    ПРУТОК and КРУГ may be shown as equivalent labels in this search artifact.
    Alias rows are marked in the comment and do not modify source data.
    """
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    rows: list[list[Any]] = []
    for result in search_results:
        comment = result.item.comment
        if result.is_profile_alias:
            note = (
                "Поисковый синоним профиля: "
                f"{result.effective_profile}; исходный профиль поставщика: "
                f"{result.item.profile}; не отдельная складская позиция"
            )
            comment = f"{comment}; {note}" if comment else note

        rows.append(
            [
                result.match_type,
                _number(result.size_delta),
                *assignment_output_row(
                    result.item,
                    profile_override=result.effective_profile,
                    comment_override=comment,
                ),
            ]
        )

    default_filters = (
        {6: _number(default_diameter)}
        if default_diameter is not None
        and any(result.item.dim1 == default_diameter for result in search_results)
        else None
    )
    _write_workbook(
        output,
        [("Поиск БрБ2 ф20", [ASSIGNMENT_SEARCH_HEADERS] + rows)],
        assignment_layout=True,
        default_filters=default_filters,
    )



def _safe_sheet_name(value: str) -> str:
    """Return an Excel-compatible worksheet name."""
    cleaned = "".join(
        "_" if char in r'[]:*?/\\' else char
        for char in str(value)
    )
    cleaned = cleaned.strip().strip("'")
    return (cleaned or "Результат")[:31]


def _write_workbook(
    output: Path,
    sheets: list[tuple[str, list[list[Any]]]],
    *,
    assignment_layout: bool = False,
    default_filters: dict[int, Any] | None = None,
) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _root_relationships())
        archive.writestr("xl/workbook.xml", _workbook_xml([name for name, _ in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships(len(sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("docProps/core.xml", _core_properties())
        archive.writestr("docProps/app.xml", _app_properties([name for name, _ in sheets]))
        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _sheet_xml(
                    rows,
                    assignment_layout=assignment_layout,
                    default_filters=default_filters,
                ),
            )


def _sheet_xml(
    rows: list[list[Any]],
    *,
    assignment_layout: bool = False,
    default_filters: dict[int, Any] | None = None,
) -> str:
    column_widths = (
        [24, 16, 18, 12, 12, 12, 18, 14, 55, 48]
        if assignment_layout
        else [24, 16, 36, 18, 12, 16, 12, 12, 12, 18, 12, 12, 14, 20, 45, 55, 48]
    )
    max_cols = max((len(row) for row in rows), default=1)
    if max_cols > len(column_widths):
        column_widths = [16, 14] + column_widths

    cols_xml = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(column_widths[:max_cols], start=1)
    )

    if assignment_layout and max_cols > len(ASSIGNMENT_HEADERS):
        numeric_columns = {2, 6, 7, 8, 10}
    elif assignment_layout:
        numeric_columns = {4, 5, 6, 8}
    else:
        numeric_columns = {5, 8, 9, 11, 13}

    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = f"{_column_name(col_index)}{row_index}"
            style = 1 if row_index == 1 else (3 if col_index in numeric_columns else 2)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                text = escape(str(value))
                cells.append(
                    f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
                )

        height = ' ht="30" customHeight="1"' if row_index == 1 else ""
        hidden = ""
        if row_index > 1 and default_filters:
            if any(
                column_index > len(row)
                or not _filter_matches(row[column_index - 1], expected)
                for column_index, expected in default_filters.items()
            ):
                hidden = ' hidden="1"'
        row_xml.append(
            f'<row r="{row_index}"{height}{hidden}>{"".join(cells)}</row>'
        )

    last_col = _column_name(max_cols)
    last_row = max(1, len(rows))
    auto_filter = _auto_filter_xml(
        last_col=last_col,
        last_row=last_row,
        default_filters=default_filters,
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols_xml}</cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  {auto_filter}
</worksheet>'''


def _auto_filter_xml(
    *,
    last_col: str,
    last_row: int,
    default_filters: dict[int, Any] | None,
) -> str:
    if not default_filters:
        return f'<autoFilter ref="A1:{last_col}{last_row}"/>'

    columns = []
    for column_index, expected in sorted(default_filters.items()):
        value = escape(str(expected))
        columns.append(
            f'<filterColumn colId="{column_index - 1}">'
            f'<filters><filter val="{value}"/></filters>'
            f'</filterColumn>'
        )
    return (
        f'<autoFilter ref="A1:{last_col}{last_row}">'
        f'{"".join(columns)}'
        f'</autoFilter>'
    )


def _filter_matches(value: Any, expected: Any) -> bool:
    try:
        return Decimal(str(value)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return str(value).strip().casefold() == str(expected).strip().casefold()


def _styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="10"/><name val="Arial"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Arial"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD9E2F3"/></left><right style="thin"><color rgb="FFD9E2F3"/></right><top style="thin"><color rgb="FFD9E2F3"/></top><bottom style="thin"><color rgb="FFD9E2F3"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _content_types(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  {overrides}
</Types>'''


def _root_relationships() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def _workbook_xml(names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets}</sheets>
</workbook>'''


def _workbook_relationships(sheet_count: int) -> str:
    sheet_rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    styles_id = sheet_count + 1
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {sheet_rels}
  <Relationship Id="rId{styles_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''


def _core_properties() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Нормализованные прайс-листы</dc:title>
  <dc:creator>price-parser</dc:creator>
</cp:coreProperties>'''


def _app_properties(names: list[str]) -> str:
    titles = "".join(f"<vt:lpstr>{escape(name)}</vt:lpstr>" for name in names)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>price-parser</Application>
  <TitlesOfParts><vt:vector size="{len(names)}" baseType="lpstr">{titles}</vt:vector></TitlesOfParts>
</Properties>'''


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _dimension_cell(
    display: str | None,
    value: Any,
) -> Any:
    if display:
        return display
    if value is None:
        return None
    return _number(value)


def _number(value: Any) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)
