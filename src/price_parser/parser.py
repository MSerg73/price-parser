from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import ParseStats, ParsedItem
from .normalization import parse_raw_item
from .suppliers import SupplierAlphaParser, GenericParser, SupplierBetaParser, SupplierGammaParser
from .suppliers.base import SupplierParser
from .workbook import load_workbook

PARSERS: tuple[SupplierParser, ...] = (
    SupplierBetaParser(),
    SupplierAlphaParser(),
    SupplierGammaParser(),
    GenericParser(),
)


def parse_files(paths: Iterable[str | Path]) -> tuple[list[ParsedItem], ParseStats]:
    items: list[ParsedItem] = []
    stats = ParseStats()

    for path in paths:
        workbook = load_workbook(path)
        stats.input_files += 1
        parser = _select_parser(workbook)
        raw_items = parser.extract(workbook)
        stats.raw_items += len(raw_items)

        for raw in raw_items:
            parsed = parse_raw_item(raw)
            items.append(parsed)
            stats.parsed_items += 1
            stats.warnings += len(parsed.warnings)

    return items, stats


def _select_parser(workbook) -> SupplierParser:
    matches = [parser for parser in PARSERS if parser.matches(workbook)]
    if not matches:
        raise ValueError(
            f"Не удалось определить поставщика и структуру файла: {workbook.path.name}"
        )
    return matches[0]
