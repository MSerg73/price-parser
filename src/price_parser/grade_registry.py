from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GradeRecord:
    canonical: str
    aliases: tuple[str, ...]
    source: str
    status: str = "confirmed"


@dataclass(frozen=True)
class GradeMatch:
    canonical: str
    raw: str
    start: int
    end: int
    source: str


# Curated registry. Only designations confirmed by the test assignment or
# metallurgy standards are included. Unknown tokens must remain unconfirmed
# and be delegated to validation/LLM instead of being guessed.
GRADE_RECORDS: tuple[GradeRecord, ...] = (
    GradeRecord(
        "БРБ2",
        ("БРБ2", "C17200", "CUBE2", "CU BE2", "ALLOY25", "ALLOY 25"),
        "Тестовое задание заказчика",
    ),
    GradeRecord("У7А", ("У7А",), "ГОСТ 1435-99"),
    GradeRecord("У8А", ("У8А",), "ГОСТ 1435-99"),
    GradeRecord("У9", ("У9",), "ГОСТ 1435-99"),
    GradeRecord("У10А", ("У10А",), "ГОСТ 1435-99"),
    GradeRecord("У12А", ("У12А",), "ГОСТ 1435-99"),
    GradeRecord("ХВГ", ("ХВГ",), "ГОСТ 5950-2000"),
    GradeRecord("Х12М", ("Х12М",), "ГОСТ 5950-2000"),
    GradeRecord("Х12МФ", ("Х12МФ",), "ГОСТ 5950-2000"),
    GradeRecord("Х12Ф1", ("Х12Ф1",), "ГОСТ 5950-2000"),
    GradeRecord("ШХ15", ("ШХ15",), "ГОСТ 801-78"),
    GradeRecord("12Х13", ("12Х13",), "ГОСТ 5632"),
    GradeRecord("20Х13", ("20Х13",), "ГОСТ 5632"),
    GradeRecord("20Х17Н2", ("20Х17Н2",), "ГОСТ 5632"),
    GradeRecord("08Х18Н10Т", ("08Х18Н10Т",), "ГОСТ 5632"),
    GradeRecord("12Х18Н10Т", ("12Х18Н10Т",), "ГОСТ 5632"),
    GradeRecord("12Х18Н9Т", ("12Х18Н9Т",), "ГОСТ 5632"),
    GradeRecord("А12", ("А12",), "ГОСТ 1414-75"),
    GradeRecord("11Р3АМ3Ф2", ("11Р3АМ3Ф2",), "ГОСТ 19265-73"),
    GradeRecord("15Х", ("15Х",), "ГОСТ 4543-2016"),
    GradeRecord("60С2А", ("60С2А",), "ГОСТ 14959-2016"),
    GradeRecord("М3Р", ("М3Р",), "ГОСТ 859"),
    GradeRecord("36КНМ", ("36КНМ",), "ГОСТ 10994-74"),
    GradeRecord("32НКД", ("32НКД",), "ГОСТ 10994-74"),
    GradeRecord("47НД", ("47НД", "47НД-ВИ"), "ГОСТ 10994-74"),
    GradeRecord("32НК", ("32НК", "32НК-ЭЛ", "32НК-ВИ"), "ГОСТ 10994-74"),
    GradeRecord("49К2Ф", ("49К2Ф",), "ГОСТ 10994-74"),
    GradeRecord("42НХТЮ", ("42НХТЮ",), "ГОСТ 10994-74"),
    GradeRecord("36НХТЮ", ("36НХТЮ",), "ГОСТ 10994-74"),
    GradeRecord("36НХТЮ5М", ("36НХТЮ5М",), "ГОСТ 10994-74"),
    GradeRecord("Х12ВМФ", ("Х12ВМФ",), "ГОСТ 5950-2000"),
    GradeRecord("Х15Ю5", ("Х15Ю5",), "ГОСТ 10994-74"),
    GradeRecord("Х23Ю5Т", ("Х23Ю5Т",), "ГОСТ 10994-74"),
    GradeRecord("НК0,2Э", ("НК0,2Э", "НК0.2Э"), "ГОСТ 13548-77 / ГОСТ 19241-80"),
    GradeRecord("НВ3", ("НВ3",), "ГОСТ 13548-77 / ГОСТ 19241-80"),
    GradeRecord("М1", ("М1",), "ГОСТ 859"),
    GradeRecord("М2", ("М2",), "ГОСТ 859"),
    GradeRecord("М3", ("М3",), "ГОСТ 859"),
    GradeRecord("40Х", ("40Х",), "ГОСТ 4543"),
    GradeRecord("09Г2С", ("09Г2С",), "ГОСТ 19281"),
    GradeRecord("СВ-08ГА", ("СВ-08ГА", "СВ08ГА"), "ГОСТ 2246-70"),
    GradeRecord("СВ-08Г2С", ("СВ-08Г2С", "СВ08Г2С"), "ГОСТ 2246-70"),
    GradeRecord("29НК", ("29НК",), "ГОСТ 10994-74"),
    GradeRecord("36Н", ("36Н",), "ГОСТ 10994-74"),
    GradeRecord("50Н", ("50Н",), "ГОСТ 10160-75"),
    GradeRecord("79НМ", ("79НМ",), "ГОСТ 10160-75"),
    GradeRecord("81НМА", ("81НМА",), "ГОСТ 10160-75"),
    GradeRecord("Х20Н80", ("Х20Н80",), "ГОСТ 10994 / ГОСТ 8803-89"),
    GradeRecord("Х15Н60", ("Х15Н60",), "ГОСТ 10994 / ГОСТ 8803-89"),
    GradeRecord("CU-ETP", ("CU-ETP",), "EN 1976 / copper designation"),
    GradeRecord("CU-DHP", ("CU-DHP",), "EN 1976 / copper designation"),
    GradeRecord("CU-OFE", ("CU-OFE", "М0Б"), "EN 1976 / ГОСТ 859"),
    GradeRecord("C10200", ("C10200", "С10200"), "UNS copper alloy designation"),
    GradeRecord("C11000", ("C11000", "С11000"), "UNS copper alloy designation"),
    GradeRecord("А75", ("А75", "A75"), "ТУ 14-1-3390-82"),
    GradeRecord("80С", ("80С",), "нормативно-справочная документация по маркам стали"),
    GradeRecord("03Х17Н14М3", ("03Х17Н14М3", "ЭИ66"), "ГОСТ 5632 / справочник обозначений ЭИ"),
    GradeRecord("015Н18М4ТЮ-ИД", ("015Н18М4ТЮ-ИД", "ЭП989-ИД", "ЧС5У"), "отраслевая НТД на сплав ЭП989/ЧС5У"),
    GradeRecord("Н70МФ", ("Н70МФ", "ЭП496"), "ГОСТ 5632 / справочник никелевых сплавов"),
    GradeRecord("84КСР", ("84КСР",), "ГОСТ 10994-74"),
    GradeRecord("32НХ3", ("32НХ3", "ЭП546"), "справочник прецизионных сплавов"),
    GradeRecord("10880", ("10880", "Э10"), "ГОСТ 11036-75"),
    GradeRecord("11895", ("11895",), "ГОСТ 11036-75"),
    GradeRecord(
        "НМЖМЦ28-2,5-1,5",
        ("НМЖМЦ28-2,5-1,5", "НМЖМЦ 28-2.5-1.5", "НМЖМЦ 28-2,5-1,5"),
        "ГОСТ 492-2006",
    ),
    GradeRecord("ТБ107/71", ("ТБ107/71",), "ГОСТ 10533-86"),
    GradeRecord("Х23Ю5", ("Х23Ю5", "Х23Ю5-М"), "ГОСТ 12766.2"),
    GradeRecord(
        "03Н18К9М5ТЮ-ИД",
        ("03Н18К9М5ТЮ-ИД", "ЧС4-ИД"),
        "ТУ 14-1-4805-90",
    ),
    GradeRecord("E308LT1-4(1)", ("E308LT1-4(1)",), "AWS A5.22"),
    GradeRecord("АВ", ("АВ",), "ГОСТ 4784"),
    GradeRecord("1.4410", ("1.4410",), "EN 10088"),
    GradeRecord("С1", ("С1",), "ГОСТ 3778-98"),
)

CANONICAL_GRADES = frozenset(record.canonical for record in GRADE_RECORDS)

# Tokens that are common service text, not material grades.
GRADE_STOPWORDS = {
    "МИН",
    "МИН.",
    "МИНИМУМ",
    "СЕРТ",
    "СЕРТ.",
    "ОПТ",
    "ЗАКАЗ",
    "ДНЕЙ",
    "ДЕНЬ",
    "КГ",
    "М",
    "ММ",
    "ШТ",
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Spaces and hyphens in catalogue text may vary.
    escaped = re.escape(alias.upper())
    escaped = escaped.replace(r"\ ", r"\s*")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(rf"(?<![0-9A-ZА-ЯЁ])({escaped})(?![0-9A-ZА-ЯЁ])", re.I)


_PATTERNS: tuple[tuple[GradeRecord, str, re.Pattern[str]], ...] = tuple(
    (record, alias, _alias_pattern(alias))
    for record in GRADE_RECORDS
    for alias in sorted(record.aliases, key=len, reverse=True)
)


def find_verified_grades(text: str) -> list[GradeMatch]:
    found: list[GradeMatch] = []
    occupied: list[tuple[int, int]] = []

    for record, _alias, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            span = match.span(1)
            if any(not (span[1] <= start or span[0] >= end) for start, end in occupied):
                continue
            raw = match.group(1)
            found.append(
                GradeMatch(
                    canonical=record.canonical,
                    raw=raw,
                    start=span[0],
                    end=span[1],
                    source=record.source,
                )
            )
            occupied.append(span)

    found.sort(key=lambda item: (item.start, -(item.end - item.start)))
    return found
