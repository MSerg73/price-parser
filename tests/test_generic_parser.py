from pathlib import Path

from price_parser.suppliers.generic import GenericParser
from price_parser.workbook import SheetData, WorkbookData


def test_generic_parser_detects_common_header_and_preserves_source() -> None:
    workbook = WorkbookData(
        path=Path("new_supplier.xlsx"),
        sheets=[
            SheetData(
                name="Прайс",
                rows=[
                    ["Прайс-лист"],
                    ["Наименование", "Марка", "Остаток", "Цена"],
                    ["Труба 12Х18Н10Т 5х1,5", "12Х18Н10Т", "20 кг", "2090"],
                ],
            )
        ],
    )

    rows = GenericParser().extract(workbook)

    assert len(rows) == 1
    row = rows[0]
    assert row.supplier == "new supplier"
    assert row.description == "Труба 12Х18Н10Т 5х1,5"
    assert row.grade_hint == "12Х18Н10Т"
    assert row.availability == "20 кг"
    assert row.price == "2090"
    assert row.source.file == "new_supplier.xlsx"
    assert row.source.sheet == "Прайс"
    assert row.source.row == 3



def test_generic_parser_prefers_price_with_vat() -> None:
    workbook = WorkbookData(
        path=Path("supplier.xlsx"),
        sheets=[
            SheetData(
                name="Прайс",
                rows=[
                    [
                        "Наименование",
                        "Цена без НДС",
                        "Цена с НДС",
                        "Остаток",
                    ],
                    ["Пруток БрБ2 ф20", "1000", "1200", "5 кг"],
                ],
            )
        ],
    )

    rows = GenericParser().extract(workbook)

    assert len(rows) == 1
    assert rows[0].price == "1200"
    assert "Из двух цен выбрана цена с НДС" in rows[0].extra["comments"]


def test_generic_parser_maps_gefesta_like_profile_and_dimensions() -> None:
    workbook = WorkbookData(
        path=Path("supplier_delta_stock.xlsx"),
        sheets=[
            SheetData(
                name="Склад наличие",
                rows=[
                    [
                        "Наименование",
                        "Вид проката",
                        "Диаметр",
                        "Размеры мм.",
                        "Остаток\n в кг",
                    ],
                    ["ЭИ868(ХН60ВТ)", "круг", "ф105", "360мм", 28.9],
                    ["ЭИ868(ХН60ВТ)", "проволока", "ф2,5", "", 6.8],
                    ["ЭИ100(20Х13Н4Г9)", "лист", "", "0,8х980х1350-1500", 745.5],
                ],
            )
        ],
    )

    raw_rows = GenericParser().extract(workbook)

    assert len(raw_rows) == 3
    assert raw_rows[0].extra["profile_hint"] == "круг"
    assert raw_rows[0].extra["structured_dimensions"] == {
        "primary": "ф105",
        "secondary": "360мм",
    }
    assert raw_rows[0].extra["availability_unit"] == "кг"

    from price_parser.normalization import parse_raw_item

    circle = parse_raw_item(raw_rows[0])
    assert circle.profile == "КРУГ"
    assert circle.grade == "ХН60ВТ"
    assert str(circle.dim1) == "105"
    assert str(circle.dim2) == "360"
    assert circle.availability == "28.9 кг"

    wire = parse_raw_item(raw_rows[1])
    assert wire.profile == "ПРОВОЛОКА"
    assert wire.grade == "ХН60ВТ"
    assert str(wire.dim1) == "2.5"

    sheet = parse_raw_item(raw_rows[2])
    assert sheet.profile == "ЛИСТ"
    assert sheet.grade == "20Х13Н4Г9"
    assert str(sheet.dim1) == "0.8"
    assert str(sheet.dim2) == "980"
    assert str(sheet.dim3) == "1350"
    assert sheet.dim3_display == "1350-1500"


def test_generic_parser_detects_late_header_and_does_not_treat_size_as_profile() -> None:
    rows = [[""] for _ in range(50)]
    rows.append(
        [
            "марки названия",
            "вид проката",
            "размер диамер / толшина",
            "длина / раскрой",
            "опысания / характеристика",
            "примичание",
            "в наличии",
        ]
    )
    rows.extend(
        [
            ["42ХНМ ЭП630У", "Круг", "ф5,6мм", "2000-2400мм", "ТУ", "", "в наличии"],
            [
                "ЭИ481Ш (37Х12Н8Г8МФБ-Ш",
                "ф20-90мм",
                "от 1000 кг",
                "ТУ 14-1-1923-76",
                "под заказ 70-90 дней",
                "",
                "",
            ],
        ]
    )
    workbook = WorkbookData(
        path=Path("new.xlsx"),
        sheets=[SheetData(name="Под заказ", rows=rows)],
    )

    raw_rows = GenericParser().extract(workbook)

    from price_parser.normalization import parse_raw_item

    exact = parse_raw_item(raw_rows[0])
    shifted = parse_raw_item(raw_rows[1])
    assert exact.profile == "КРУГ"
    assert str(exact.dim1) == "5.6"
    assert str(exact.dim2) == "2000"
    assert shifted.profile == "НЕ УКАЗАН"
    assert str(shifted.dim1) == "20"
    assert shifted.availability == "под заказ 70-90 дней"
    assert "profile_unparsed" in shifted.review_reasons
