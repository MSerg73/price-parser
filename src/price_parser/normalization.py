from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .domain_routing import classify_domain
from .grade_registry import CANONICAL_GRADES, GRADE_STOPWORDS, find_verified_grades
from .models import ParsedItem, RawItem
from .reference_rules import (
    display_name as build_display_name,
    inch_fraction,
    mesh_attributes,
    parse_quantity,
    reference_hint_for,
    scrap_attributes,
)


PROFILE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("ТРУБА", ("труб",)),
    ("ЛИСТ", ("лист",)),
    ("ПЛИТА", ("плит",)),
    ("КРУГ", ("круг", "кругляк")),
    ("ПРУТОК", ("прут",)),
    ("ПРОВОЛОКА", ("проволок",)),
    ("ЛЕНТА", ("лент",)),
    ("ПОЛОСА", ("полос",)),
    ("КВАДРАТ", ("квадрат",)),
    ("ШЕСТИГРАННИК", ("шестигран",)),
    ("ПОКОВКА", ("поковк",)),
    ("УГОЛОК", ("угол",)),
    ("ШВЕЛЛЕР", ("швеллер",)),
    ("АНОД", ("анод",)),
    ("КАТОД", ("катод",)),
    ("ФОЛЬГА", ("фольг",)),
    ("ШИНА", ("шин",)),
    ("ГРАНУЛЫ", ("гранул",)),
    ("ВТУЛКА", ("втулк",)),
    ("ДИСК", ("диск",)),
    ("КОЛЬЦО", ("кольц",)),
    ("ПРОВОДНИК", ("проводник",)),
    ("СЕТКА", ("сетк",)),
    ("ОТВОД", ("отвод",)),
    ("ТРОЙНИК", ("тройник",)),
    ("ПЕРЕХОД", ("переход",)),
    ("ФЛАНЕЦ", ("флан",)),
    ("БОЛТ", ("болт",)),
    ("ВИНТ", ("винт",)),
    ("ГАЙКА", ("гайк",)),
    ("ШАЙБА", ("шайб",)),
    ("ШПИЛЬКА", ("шпильк",)),
    ("ЭЛЕКТРОД", ("электрод",)),
    ("ЗАГОТОВКА", ("заготовк",)),
    ("СЛИТОК", ("слит",)),
    ("ШИХТА", ("шихт",)),
    ("ЛОМ", ("лом",)),
]


KNOWN_PROFILES = frozenset(profile for profile, _aliases in PROFILE_PATTERNS)

# Materials are recognized only when a source token is explicitly present.
# RC14 intentionally does not generate translations, chemical symbols or
# inferred synonyms such as "Титан -> titanium/Ti".
MATERIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ТИТАН",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])титан(?:а|у|ом|е)?"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "МЕДЬ",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])(?:медь|меди|медью)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "ЛАТУНЬ",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])(?:латунь|латуни|латунью)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "БРОНЗА",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])(?:бронза|бронзы|бронзе|бронзой)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "АЛЮМИНИЙ",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])"
            r"(?:алюминий|алюминия|алюминию|алюминием|алюминии)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "НИКЕЛЬ",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])"
            r"(?:никель|никеля|никелю|никелем|никеле)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
    (
        "СТАЛЬ",
        re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё])(?:сталь|стали|сталью)"
            r"(?![0-9A-Za-zА-Яа-яЁё])",
            re.I,
        ),
    ),
)


DIMENSION_OPTIONAL_PROFILES = {"ЛОМ", "ШИХТА"}

# Search-only equivalence. Source profile labels remain unchanged in the
# normalized table, but a request for either round bar name must search both.
SEARCH_PROFILE_EQUIVALENCE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"ПРУТОК", "КРУГ"}),
)

EXPLICIT_UNVERIFIED_DESIGNATIONS = {
    "70С3",
    "42НКД",
    "ЭП847",
}

GRADE_EQUIVALENTS = {
    "C17200": "БРБ2",
    "ALLOY25": "БРБ2",
    "ALLOY 25": "БРБ2",
    "CUBE2": "БРБ2",
    "CU BE2": "БРБ2",
}

STANDARD_TOKENS = (
    "ГОСТ", "ОСТ", "ТУ", "DIN", "ASTM", "EN", "AISI", "ДКР", "ДПР", "МЯГК",
    "ТВЕРД", "КАЛИБР", "СЕРЕБРЯНК", "ЗАКАЗ", "МЕХ.", "МКК", "УЗК", "ДЛ.",
)

SERVICE_LINE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(итого|всего|наименование|склад|прайс-лист|цены оптовые)\b", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)*\s+[^0-9]*$", re.I),
]


def normalize_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# Cyrillic/Latin confusables are unified only in a technical comparison key.
# The displayed value remains a verified canonical grade or an explicit
# project decision.
_CYRILLIC_TO_LATIN_CONFUSABLES = str.maketrans(
    {
        "\u0410": "A",
        "\u0412": "B",
        "\u0415": "E",
        "\u041a": "K",
        "\u041c": "M",
        "\u041d": "H",
        "\u041e": "O",
        "\u0420": "P",
        "\u0421": "C",
        "\u0422": "T",
        "\u0423": "Y",
        "\u0425": "X",
    }
)

# Keys are grade_match_key() values. Values are customer-facing spellings.
_GRADE_LAYOUT_OVERRIDES = {
    "A75": "\u041075",
}


def grade_match_key(raw: object) -> str:
    value = unicodedata.normalize("NFKC", normalize_space(raw))
    value = value.upper().replace("\u0401", "\u0415")
    value = (
        value.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )
    value = value.strip("[];,()")
    value = value.translate(_CYRILLIC_TO_LATIN_CONFUSABLES)
    return re.sub(r"[\s_-]+", "", value)


def _canonical_string(candidate: object) -> str | None:
    if isinstance(candidate, str):
        return normalize_space(candidate) or None

    if isinstance(candidate, dict):
        for field in ("canonical", "grade", "name", "value"):
            value = candidate.get(field)
            if isinstance(value, str) and normalize_space(value):
                return normalize_space(value)
        return None

    for field in ("canonical", "grade", "name", "value"):
        value = getattr(candidate, field, None)
        if isinstance(value, str) and normalize_space(value):
            return normalize_space(value)

    return None


def _registry_canonical_by_key() -> dict[str, str]:
    source = CANONICAL_GRADES
    candidates = list(source.values()) if isinstance(source, dict) else list(source)

    buckets: dict[str, set[str]] = {}
    for candidate in candidates:
        canonical = _canonical_string(candidate)
        if not canonical:
            continue
        key = grade_match_key(canonical)
        if key:
            buckets.setdefault(key, set()).add(canonical)

    return {
        key: next(iter(values))
        for key, values in buckets.items()
        if len(values) == 1
    }


def canonicalize_grade_layout(value: str) -> str:
    cleaned = normalize_space(value).upper().replace("\u0401", "\u0415")
    key = grade_match_key(cleaned)

    override = _GRADE_LAYOUT_OVERRIDES.get(key)
    if override:
        return override

    registry_value = _registry_canonical_by_key().get(key)
    if registry_value:
        return registry_value

    return cleaned


def canonical_profile(text: str) -> str:
    lower = text.lower().replace("ё", "е")
    for profile, aliases in PROFILE_PATTERNS:
        if any(re.search(rf"\b{re.escape(alias)}\w*\b", lower) for alias in aliases):
            return profile
    first = normalize_space(text).split(" ", 1)[0].strip(".,:;()[]").upper()
    return first if first else "НЕ ОПРЕДЕЛЁН"


def search_profile_variants(value: str) -> frozenset[str]:
    """Return profiles treated as equivalent only while searching.

    ``КРУГ`` and ``ПРУТОК`` remain separate source labels in exported data.
    For customer requests they form one round-bar search group, because both
    use diameter as ``Размер 1`` in the test assignment.
    """
    profile = canonical_profile(value)
    for group in SEARCH_PROFILE_EQUIVALENCE_GROUPS:
        if profile in group:
            return group
    return frozenset({profile})


def is_service_line(text: str, numeric_values_present: bool = False) -> bool:
    value = normalize_space(text)
    if not value:
        return True
    for pattern in SERVICE_LINE_PATTERNS:
        if pattern.search(value):
            if numeric_values_present and len(value.split()) >= 2:
                return False
            return True
    if re.match(r"^\s*\d+(?:\.\d+)*\s+", value) and not numeric_values_present:
        return True
    return False


def normalize_grade(raw: str | None) -> tuple[str, list[str]]:
    comments: list[str] = []
    raw_value = normalize_space(raw).strip("[];,")
    if not raw_value:
        return "предпол.", comments

    raw_upper = raw_value.upper().replace("Ё", "Е")

    # v0.2.6: separate the T1 supply condition from alloy grade АВ.
    # The rule is targeted; generic suffix stripping is unsafe.
    av_t1 = re.fullmatch(
        r"(?:АВ|AB)[.\s_-]*(?:Т1|T1)",
        raw_upper,
        re.I,
    )
    if av_t1:
        comments.append("Состояние поставки: Т1")
        if raw_value != "АВ":
            comments.append(f"Исходное обозначение поставщика: {raw_value}")
        return "АВ", comments

    # Supplier alloy columns may append temper/specification markers to Cu-ETP.
    # Keep the material grade in the grade column and move the suffix to comment.
    cu_etp = re.match(r"^(CU-ETP)(?:\s+(.+))?$", raw_upper)
    if cu_etp:
        suffix = normalize_space(cu_etp.group(2))
        if suffix:
            comments.append(f"Дополнительное обозначение марки: {suffix}")
        return "CU-ETP", comments

    verified_hint_matches = find_verified_grades(raw_value)
    for verified_hint in verified_hint_matches:
        if verified_hint.start == 0 and verified_hint.end == len(raw_value):
            if verified_hint.canonical.upper() != raw_value.upper():
                comments.append(f"Исходное обозначение поставщика: {raw_value}")
            return verified_hint.canonical, comments

    value = raw_value.strip("()")
    upper = value.upper().replace("Ё", "Е")
    compact = re.sub(r"[\s_-]+", "", upper)
    if upper in GRADE_EQUIVALENTS:
        comments.append(f"Исходное обозначение поставщика: {value}")
        return GRADE_EQUIVALENTS[upper], comments
    if compact in GRADE_EQUIVALENTS:
        comments.append(f"Исходное обозначение поставщика: {value}")
        return GRADE_EQUIVALENTS[compact], comments

    canonical = canonicalize_grade_layout(upper)
    if canonical != upper:
        comments.append(f"Исходное обозначение поставщика: {value}")
    return canonical, comments


def parse_price(value: object) -> Decimal | None:
    text = normalize_space(value)
    if not text:
        return None
    cleaned = (
        text.replace("руб.", "")
        .replace("руб", "")
        .replace("₽", "")
        .replace("'", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def format_availability(value: object, default_unit: str | None = None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float, Decimal)):
        text = _number_text(value)
        return f"{text} {default_unit}".strip() if default_unit else text
    text = normalize_space(value)
    if default_unit and re.fullmatch(r"-?\d+(?:[.,]\d+)?", text):
        return f"{text.replace(',', '.')} {default_unit}"
    return text


def extract_grade_from_description(description: str, profile: str) -> tuple[str, list[str]]:
    text = normalize_space(description)
    comments: list[str] = []

    parenthesised = re.findall(r"\(([^()]{2,40})\)", text)
    for alt in parenthesised:
        cleaned_alt = normalize_space(alt)
        if _is_service_parenthetical(cleaned_alt):
            continue
        if re.search(r"[A-Za-zА-Яа-я]", cleaned_alt):
            comments.append(f"Альтернативное обозначение: {cleaned_alt}")

    # Aluminium alloy 1340/АВ is accepted only in explicit alloy context.
    # The number 1340 is otherwise a common sheet dimension.
    if (
        re.search(r"СПЛАВ\s+АЛЮМ", text, re.I)
        and re.search(r"\(\s*1340\s*\)", text)
        and re.search(r"\bАВ(?:[.\s-]*Т1)?\b", text, re.I)
    ):
        comments.append("Цифровое обозначение сплава: 1340")
        if re.search(r"\bАВ[.\s-]*Т1\b", text, re.I):
            comments.append("Состояние поставки: Т1")
        return "АВ", comments

    # Composite notation such as 08(12)Х18Н10Т means two explicitly written grades.
    composite = re.search(
        r"(?<![0-9А-ЯA-Z])(\d{2})\((\d{2})\)"
        r"([ХхXx][0-9А-Яа-яA-Za-z-]+)",
        text,
    )
    if composite:
        tail = composite.group(3).upper().replace("X", "Х")
        primary_candidate = f"{composite.group(1)}{tail}"
        alternative_candidate = f"{composite.group(2)}{tail}"
        if (
            primary_candidate in CANONICAL_GRADES
            and alternative_candidate in CANONICAL_GRADES
        ):
            comments.append(f"Альтернативная марка: {alternative_candidate}")
            return primary_candidate, comments

    # Prefer grades confirmed by the curated metallurgy registry.
    # The first occurrence is primary; subsequent confirmed grades are comments.
    verified_matches = find_verified_grades(text)
    if verified_matches:
        primary = verified_matches[0]
        primary_raw_normalized = normalize_space(primary.raw).upper().replace("Ё", "Е")
        canonical_normalized = primary.canonical.upper().replace("Ё", "Е")
        if primary_raw_normalized != canonical_normalized:
            comments.append(f"Исходное обозначение поставщика: {primary.raw}")
        for alternative in verified_matches[1:]:
            if alternative.canonical != primary.canonical:
                comments.append(
                    f"Альтернативная марка: {alternative.canonical} "
                    f"(исходное обозначение: {alternative.raw})"
                )
            elif normalize_space(alternative.raw).upper() != primary_raw_normalized:
                comments.append(
                    f"Альтернативное обозначение марки: {alternative.raw}"
                )
        return primary.canonical, comments

    # Fasteners: M12x75 is a size, while A2-70/A4-70 is the material grade.
    if profile in {"БОЛТ", "ВИНТ", "ГАЙКА", "ШАЙБА", "ШПИЛЬКА"}:
        fastener_grade = re.search(r"\b([AА][24]-\d{2})\b", text, re.I)
        if fastener_grade:
            return normalize_grade(fastener_grade.group(1))[0], comments

    # International designation explicitly written in the row.
    aisi_mark = re.search(r"\bAISI\s*\d{3}[A-Z]?\b", text, re.I)
    if aisi_mark:
        return normalize_grade(aisi_mark.group(0))[0], comments

    # Explicit steel grade notation, e.g. "ст.20".
    steel_mark = re.search(r"\bст\.?\s*(\d{1,3}[A-Za-zА-Яа-я0-9-]*)", text, re.I)
    if steel_mark:
        return normalize_grade(steel_mark.group(1))[0], comments

    # The token immediately after the profile is often the grade in 1C exports:
    # "Труба М1 12х1", "Пруток Л63 № 7", "Лента БРОФ 6,5-0,15".
    after_profile = _text_after_profile(text, profile)
    split_alloy = re.match(
        r"^\s*(БР[А-ЯA-Z]+|ЛЖМЦ|АМФ|АМГ|ШМТ|ММ|МТ|ХВГ)\s+"
        r"(\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)+)",
        after_profile,
        re.I,
    )
    if split_alloy:
        grade = f"{split_alloy.group(1)}{split_alloy.group(2)}"
        return normalize_grade(grade)[0], comments

    first_token_match = re.match(r"^\s*([0-9A-Za-zА-Яа-яЁё-]+)", after_profile)
    if first_token_match:
        first_token = first_token_match.group(1)
        if _looks_like_leading_grade(first_token, after_profile, profile):
            return normalize_grade(first_token)[0], comments

    # Full Russian steel grades such as 12Х18Н10Т, 20Х13, 95Х18-Ш.
    # Prefer the last candidate because the first numeric sequence is usually size.
    full_x_grades = re.findall(
        r"(?<![\w.,])(\d{1,3}[ХхXx][0-9А-Яа-яA-Za-z-]+)",
        text,
    )
    for candidate in reversed(full_x_grades):
        if re.fullmatch(
            r"\d+(?:[.,]\d+)?[ХхXx]\d+(?:[.,]\d+)?",
            candidate,
        ):
            # A pure A×B block is a dimension, not a material grade.
            continue
        tail = re.split(r"[ХхXx]", candidate, maxsplit=1)[1]
        has_letter_after_x = bool(re.search(r"[A-Za-zА-Яа-я]", tail))
        has_separate_size = bool(
            re.search(r"(?:[ØФфДд]\s*[=:.-]?\s*\d+|[№N]\s*0*\d+)", text, re.I)
            or re.search(r"\d+(?:[.,]\d+)?\s*[×*]\s*\d+", text)
        )
        if has_letter_after_x or has_separate_size:
            return normalize_grade(candidate)[0], comments

    # Tool steels like Х12 after an explicit size.
    x_only_grade = re.search(
        r"(?:[ØФфДд]\s*[=:.-]?\s*\d+(?:[.,]\d+)?|"
        r"\d+(?:[.,]\d+)?(?:\s*[×*]\s*\d+(?:[.,]\d+)?)+)"
        r".*?\b([ХхXx]\d{1,3})\b",
        text,
    )
    if x_only_grade:
        return normalize_grade(x_only_grade.group(1))[0], comments

    # Remaining common alloy tokens with letters only.
    cleaned = text
    for _, aliases in PROFILE_PATTERNS:
        for alias in aliases:
            cleaned = re.sub(rf"\b{re.escape(alias)}\w*\b", " ", cleaned, flags=re.I)
    tokens = re.findall(r"\b[0-9A-Za-zА-Яа-яЁё-]{1,30}\b", cleaned)
    for token in tokens:
        up = token.upper()
        if up in GRADE_STOPWORDS:
            continue
        if any(up.startswith(prefix) for prefix in STANDARD_TOKENS):
            continue
        if up in {"ММ", "МТ", "АМФ", "ХВГ", "ШМТ"}:
            return normalize_grade(token)[0], comments
        if re.match(
            r"^(БР|ЛЖМЦ|ЛО|ЛС|Л|ММ|МТ|АМФ|АМГ|Д16|ВТ|ХВГ|ХН|Р)"
            r"[A-ZА-ЯЁ0-9-]*$",
            up,
        ):
            return normalize_grade(token)[0], comments

    upper_text = text.upper().replace("Ё", "Е")
    for designation in sorted(EXPLICIT_UNVERIFIED_DESIGNATIONS, key=len, reverse=True):
        if re.search(
            rf"(?<![0-9A-ZА-ЯЁ]){re.escape(designation)}(?![0-9A-ZА-ЯЁ])",
            upper_text,
        ):
            comments.append(
                "Обозначение явно указано поставщиком, но нормативное "
                "соответствие пока не подтверждено"
            )
            return designation, comments

    return "предпол.", comments



def _is_service_parenthetical(value: str) -> bool:
    upper = value.upper()
    if re.match(r"^(?:МИН\.?|МИНИМУМ)\s*", upper):
        return True
    if re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:КГ|М|ММ|ШТ\.?)", upper):
        return True
    if upper in GRADE_STOPWORDS:
        return True
    return False


def _text_after_profile(text: str, profile: str) -> str:
    for canonical, aliases in PROFILE_PATTERNS:
        if canonical != profile:
            continue
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\w*\b", text, re.I)
            if match:
                return text[match.end():]
    return text


def _looks_like_leading_grade(token: str, remainder: str, profile: str) -> bool:
    up = token.upper()
    if re.search(r"\d\s*[xх×*]\s*\d", token, re.I):
        return False
    if re.match(r"^(?:D|Д|N|№)\d", up):
        return False
    if re.fullmatch(r"\d+(?:[.,]\d+)?", token):
        return token in {"10", "20"} and not re.search(r"[×*]", remainder[:20])
    if profile in {"БОЛТ", "ВИНТ", "ГАЙКА", "ШАЙБА", "ШПИЛЬКА"} and re.match(r"^М\d", up):
        return False
    if up in {"ММ", "МТ", "АМФ", "ХВГ", "ШМТ"}:
        return True
    if re.match(
        r"^(?:БР|ЛЖМЦ|ЛО|ЛС|Л|АМФ|АМГ|Д16|ВТ|ХВГ|ХН|Р|"
        r"\d{1,3}[ХНМТВГСФКЮДРЛАБЦИПЭЧ])",
        up,
    ):
        return True
    return False



_DIMENSION_NUMBER = r"\d+(?:\.\d+)?"
_DIMENSION_ATOM = rf"{_DIMENSION_NUMBER}(?:\s*\(\s*{_DIMENSION_NUMBER}\s*\))?"
# A range hyphen is attached to the preceding number. A spaced " - " is
# treated as a separator between two fitting sizes, not as a numeric range.
_DIMENSION_COMPONENT = rf"{_DIMENSION_ATOM}(?:-\s*{_DIMENSION_ATOM})*"
_DIMENSION_SEQUENCE_BODY = (
    rf"{_DIMENSION_COMPONENT}"
    rf"(?:\s*[xх×*]\s*{_DIMENSION_COMPONENT}){{1,3}}"
    rf"(?:\s*\+\s*{_DIMENSION_ATOM})?"
)
_DIMENSION_SEQUENCE_RE = re.compile(
    rf"(?<![\w])(?:[Тт]\.?\s*)?"
    rf"({_DIMENSION_SEQUENCE_BODY})"
    rf"(?=$|[\s(),;/]|"
    rf"[xх×*]\s*(?!\d)[A-Za-zА-Яа-яЁё]|"
    rf"-\s*(?!\d)[A-Za-zА-Яа-яЁё])",
    re.I,
)
_MULTIPLE_DIMENSION_SETS_RE = re.compile(
    rf"(?<![\w])(?:[Тт]\.?\s*)?"
    rf"({_DIMENSION_COMPONENT}(?:\s*[xх×*]\s*{_DIMENSION_COMPONENT}){{1,2}})"
    rf"\s*(?:\+|\s-\s)\s*"
    rf"({_DIMENSION_COMPONENT}(?:\s*[xх×*]\s*{_DIMENSION_COMPONENT}){{1,2}})",
    re.I,
)


def _dimension_grade_spans(text: str) -> list[tuple[int, int]]:
    spans = [
        match.span()
        for match in re.finditer(
            r"(?<![\w.,])\d{1,3}[ХхXx]\d+"
            r"(?:[НнМмТтВвГгСсФфКкЮюДдРрЛлАаБбЦцИиПпЭэЧч]\d*)+",
            text,
        )
    ]
    spans.extend((match.start, match.end) for match in find_verified_grades(text))
    return spans


def _normalize_dimension_token(token: str) -> str:
    value = normalize_space(token).replace(",", ".")
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"\s*\(\s*", "(", value)
    value = re.sub(r"\s*\)\s*", ")", value)
    value = re.sub(r"\s*\+\s*", "+", value)
    return value


def _dimension_token_value(token: str) -> Decimal | None:
    match = re.search(_DIMENSION_NUMBER, token)
    return _to_decimal(match.group(0)) if match else None


def _split_dimension_sequence(value: str) -> tuple[list[Decimal], list[str]] | None:
    displays = [
        _normalize_dimension_token(part)
        for part in re.split(r"\s*[xх×*]\s*", value)
    ]
    values = [_dimension_token_value(part) for part in displays]
    if not all(item is not None for item in values):
        return None
    return [item for item in values if item is not None], displays


def _find_multiple_dimension_sets(
    text: str,
    grade_spans: list[tuple[int, int]] | None = None,
) -> tuple[tuple[list[Decimal], list[str]], tuple[list[Decimal], list[str]]] | None:
    spans = grade_spans if grade_spans is not None else _dimension_grade_spans(text)
    for match in _MULTIPLE_DIMENSION_SETS_RE.finditer(text):
        if any(
            not (match.end() <= start or match.start() >= end)
            for start, end in spans
        ):
            continue
        first = _split_dimension_sequence(match.group(1))
        second = _split_dimension_sequence(match.group(2))
        if first and second:
            return first, second
    return None


def _find_dimension_sequences(
    text: str,
    grade_spans: list[tuple[int, int]] | None = None,
) -> list[tuple[list[Decimal], list[str]]]:
    spans = grade_spans if grade_spans is not None else _dimension_grade_spans(text)
    result: list[tuple[list[Decimal], list[str]]] = []

    for match in _DIMENSION_SEQUENCE_RE.finditer(text):
        if any(
            not (match.end() <= start or match.start() >= end)
            for start, end in spans
        ):
            continue

        parsed = _split_dimension_sequence(match.group(1))
        if parsed:
            result.append(parsed)
    return result


def extract_dimension_displays(
    description: str,
    profile: str,
) -> tuple[str | None, str | None, str | None]:
    """Return source-faithful dimension text when a numeric cell is insufficient.

    Scalar dimensions continue to be exported as numbers. Ranges and explicit
    alternatives are kept as text so the assignment output does not silently
    discard supplier information.
    """
    text = normalize_space(description).replace(",", ".")
    grade_spans = _dimension_grade_spans(text)
    multiple_sets = _find_multiple_dimension_sets(text, grade_spans)
    if multiple_sets:
        _, displays = multiple_sets[0]
    else:
        sequences = _find_dimension_sequences(text, grade_spans)
        if not sequences:
            return None, None, None
        _, displays = sequences[0]

    result: list[str | None] = []
    for token in displays[:3]:
        result.append(
            token if "-" in token or "(" in token or "+" in token else None
        )
    while len(result) < 3:
        result.append(None)
    return result[0], result[1], result[2]


def extract_dimensions(description: str, profile: str) -> tuple[Decimal | None, Decimal | None, Decimal | None, list[str]]:
    text = normalize_space(description).replace(",", ".")
    warnings: list[str] = []
    grade_spans = _dimension_grade_spans(text)
    multiple_sets = _find_multiple_dimension_sets(text, grade_spans)
    dimension_sequences = _find_dimension_sequences(text, grade_spans)
    sequences = [values for values, _ in dimension_sequences]

    if multiple_sets:
        (first_values, first_displays), (_, second_displays) = multiple_sets
        warnings.append(
            "Требуется проверка: в строке несколько размерных наборов; "
            f"используется первый {'*'.join(first_displays)}, "
            f"дополнительный {'*'.join(second_displays)}"
        )
        return (
            first_values[0] if first_values else None,
            first_values[1] if len(first_values) > 1 else None,
            first_values[2] if len(first_values) > 2 else None,
            warnings,
        )

    if sequences and len(sequences[0]) > 3:
        warnings.append(
            "Дополнительный четвёртый размер сохранён в исходном описании: "
            + _normalize_dimension_token(
                dimension_sequences[0][1][3]
            )
        )

    # Strip: first value is thickness, second is width, optional third is length.
    if profile == "ПОЛОСА" and sequences:
        seq = sequences[0]
        return (
            seq[0],
            seq[1] if len(seq) > 1 else None,
            seq[2] if len(seq) > 2 else None,
            warnings,
        )

    # Tape: thickness and width are the first two values immediately after
    # the profile. Stop before a following grade that may begin with Х.
    if profile == "ЛЕНТА" and sequences:
        seq = sequences[0]
        return (
            seq[0],
            seq[1] if len(seq) > 1 else None,
            seq[2] if len(seq) > 2 else None,
            warnings,
        )

    # Hollow conductor: outside section A x B and inner channel ID.
    if profile == "ПРОВОДНИК":
        conductor_match = re.search(
            r"\bOD\s*(\d+(?:\.\d+)?)\s*[xх×*]\s*(\d+(?:\.\d+)?)"
            r".*?\bID\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if conductor_match:
            warnings.append(
                "Размеры: наружное сечение OD и внутренний канал ID; "
                "единица сохранена из исходной строки"
            )
            return (
                _to_decimal(conductor_match.group(1)),
                _to_decimal(conductor_match.group(2)),
                _to_decimal(conductor_match.group(3)),
                warnings,
            )

    # Circular blanks sold as sheet/disc: thickness and diameter are explicit,
    # while the normalized profile remains a customer business rule.
    if profile in {"ЛИСТ", "ДИСК"} and re.search(r"\bдиск\b", text, re.I):
        disc_match = re.search(
            r"(?:\bт\.?\s*)?(\d+(?:\.\d+)?)\s*\*?\s*"
            r"(?:диам\.?|[dдØ])\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if disc_match:
            warnings.append(
                "Требуется решение заказчика: нормализованный профиль ЛИСТ или ДИСК"
            )
            return (
                _to_decimal(disc_match.group(1)),
                _to_decimal(disc_match.group(2)),
                None,
                warnings,
            )

    # Flange: DN is the primary nominal size; PN is retained in comments.
    if profile == "ФЛАНЕЦ":
        flange_match = re.search(
            r"\bДу\s*(\d+(?:\.\d+)?)\b.*?\bРу\s*(\d+(?:\.\d+)?)\b",
            text,
            re.I,
        )
        if flange_match:
            warnings.append(
                f"Номинальный размер Ду{flange_match.group(1)}; "
                f"номинальное давление Ру{flange_match.group(2)}"
            )
            return _to_decimal(flange_match.group(1)), None, None, warnings

    # Mesh: width is the only dimensional value consistently present.
    if profile == "СЕТКА":
        width_match = re.search(
            r"(?:\bшир\.?|\bш\.)\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if width_match:
            warnings.append(f"Ширина сетки: {width_match.group(1)} мм")
            return _to_decimal(width_match.group(1)), None, None, warnings

    # Hexagon S is the across-flats size. The export convention is pending.
    if profile == "ШЕСТИГРАННИК":
        s_match = re.search(r"\bS\s*(\d+(?:\.\d+)?)\b", text, re.I)
        if s_match:
            warnings.append(
                "Требуется решение заказчика: размер S записан как размер 1"
            )
            return _to_decimal(s_match.group(1)), None, None, warnings

    # Ring-shaped plate: thickness, outside diameter, inside diameter.
    if profile in {"ПЛИТА", "КОЛЬЦО"}:
        ring_match = re.search(
            r"(\d+(?:\.\d+)?)\s*[xх×*]\s*[dд]\s*(\d+(?:\.\d+)?)"
            r"\s*/\s*[dд]\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if ring_match:
            warnings.append(
                "Требуется решение заказчика: нормализованный профиль ПЛИТА или КОЛЬЦО"
            )
            return (
                _to_decimal(ring_match.group(1)),
                _to_decimal(ring_match.group(2)),
                _to_decimal(ring_match.group(3)),
                warnings,
            )

    # ГОСТ 15835 notation: the number is rod diameter, НД is non-random length.
    if profile == "ПРУТОК":
        nd_match = re.search(
            r"(?<![\w.])(\d+(?:\.\d+)?)\s*[xх×*]\s*НД\b",
            text,
            re.I,
        )
        if nd_match:
            supply = re.search(r"\b(ДКРПТ|ПКРХХ)\b", text, re.I)
            note = "НД — немерная длина"
            if supply:
                note += f"; обозначение поставки: {supply.group(1).upper()}"
            warnings.append(note)
            return _to_decimal(nd_match.group(1)), None, None, warnings

    # Tube size immediately after the profile, before a following grade.
    if profile == "ТРУБА":
        tube_text = _text_after_profile(text, profile)
        direct_tube = re.match(
            r"(?!\s*\d{1,3}[ХхXx]\d+[A-Za-zА-Яа-я])"
            r"\s*(?:Ø|[Фф])?\s*(\d+(?:\.\d+)?)"
            r"\s*[xх×*]\s*(\d+(?:\.\d+)?)"
            r"(?=\s|$|[(),;])",
            tube_text,
            re.I,
        )
        if direct_tube:
            return (
                _to_decimal(direct_tube.group(1)),
                _to_decimal(direct_tube.group(2)),
                None,
                warnings,
            )

    # Tube notation with an explicit diameter prefix: ф70*17.5.
    if profile == "ТРУБА":
        prefixed_tube = re.search(
            r"(?:Ø|[Фф]|[Дд]\s*[=:.-]?)\s*(\d+(?:\.\d+)?)"
            r"\s*[xх×*]\s*(\d+(?:\.\d+)?)",
            text,
        )
        if prefixed_tube:
            return (
                _to_decimal(prefixed_tube.group(1)),
                _to_decimal(prefixed_tube.group(2)),
                None,
                warnings,
            )

    # v0.3.2: inch dimensions remain in the supplier unit. The decimal
    # inch value is stored in dim1; millimetres are only a reference attribute.
    if profile == "ТРУБА":
        inch = inch_fraction(text)
        if inch:
            display, value_inch, value_mm = inch
            warnings.append(
                f"Размер в исходной единице: {display} дюйма; "
                f"справочно {value_mm.normalize()} мм"
            )
            return value_inch, None, None, warnings

    # Metric fasteners: M12x75 means thread diameter and length, not metal grade.
    metric_match = re.search(
        r"\bМ\s*(\d+(?:\.\d+)?)\s*[xх×*]\s*(\d+(?:\.\d+)?)",
        text,
        re.I,
    )
    if metric_match and profile in {"БОЛТ", "ВИНТ", "ШПИЛЬКА"}:
        return (
            _to_decimal(metric_match.group(1)),
            _to_decimal(metric_match.group(2)),
            None,
            warnings,
        )
    metric_single = re.search(r"\bМ\s*(\d+(?:\.\d+)?)", text, re.I)
    if metric_single and profile in {"ГАЙКА", "ШАЙБА"}:
        return _to_decimal(metric_single.group(1)), None, None, warnings

    # Explicit OD/ID notation for tubes. Size 1 is outside diameter;
    # size 2 is deterministically calculated wall thickness.
    if profile == "ТРУБА":
        od_match = re.search(r"\bO\.?D\.?\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.I)
        id_match = re.search(r"\bI\.?D\.?\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.I)
        length_match = re.search(
            r"(?:длина|\bL)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:мм|mm)?",
            text,
            re.I,
        )
        if od_match:
            outside = _to_decimal(od_match.group(1))
            inside = _to_decimal(id_match.group(1)) if id_match else None
            length = _to_decimal(length_match.group(1)) if length_match else None
            wall = None
            if outside is not None and inside is not None:
                if outside > inside:
                    wall = (outside - inside) / Decimal("2")
                    warnings.append(
                        "Толщина стенки рассчитана из OD/ID: "
                        f"({outside} - {inside}) / 2 = {wall} мм"
                    )
                else:
                    warnings.append(
                        f"Некорректная пара OD/ID: OD={outside}, ID={inside}"
                    )
            elif inside is not None:
                warnings.append(f"Указан ID={inside} мм без наружного диаметра")
            return outside, wall, length, warnings

    # Explicit diameter notation.
    diameter_match = re.search(r"(?:Ø|[Фф]|[Дд]\s*[=:.-]?)\s*(\d+(?:\.\d+)?)", text)
    explicit_diameter = _to_decimal(diameter_match.group(1)) if diameter_match else None

    # N008 or № 8 often means section size in Russian price lists.
    number_size_match = re.search(r"(?:№|N)\s*0*(\d+(?:\.\d+)?)", text, re.I)
    number_size = _to_decimal(number_size_match.group(1)) if number_size_match else None

    # Round bar group: КРУГ and ПРУТОК use the same dimension order.
    # dim1 is diameter; dim2 is an explicitly supplied second size (normally
    # length). Source profile labels remain unchanged.
    if profile in {"КРУГ", "ПРУТОК"}:
        prefixed_pair = re.search(
            r"(?:Ø|[Фф]|[Дд]\s*[=:.-]?)\s*(\d+(?:\.\d+)?)"
            r"\s*[xх×*]\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if prefixed_pair:
            return (
                _to_decimal(prefixed_pair.group(1)),
                _to_decimal(prefixed_pair.group(2)),
                None,
                warnings,
            )

        length_match = re.search(
            r"(?:\bдлина\b|\bL)\s*[:=.-]?\s*(\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        explicit_second = (
            _to_decimal(length_match.group(1))
            if length_match
            else None
        )

        ambiguous_t = re.search(
            r"(?<![А-Яа-яA-Za-z])(?:т\.?|толщ(?:ина)?)\s*"
            r"(\d+(?:[.,]\d+)?)",
            text,
            re.I,
        )
        if explicit_second is None and ambiguous_t:
            explicit_second = _to_decimal(ambiguous_t.group(1))
            warnings.append(
                "Обозначение второго размера через «т.» сохранено в Размер 2; "
                "его роль требует проверки по исходному прайсу"
            )

        if explicit_diameter is not None:
            return explicit_diameter, explicit_second, None, warnings
        if number_size is not None:
            return number_size, explicit_second, None, warnings
        if sequences:
            seq = sequences[0]
            return (
                seq[0],
                seq[1] if len(seq) > 1 else explicit_second,
                seq[2] if len(seq) > 2 else None,
                warnings,
            )
        mm_match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*мм\b", text, re.I)
        if mm_match:
            return _to_decimal(mm_match.group(1)), explicit_second, None, warnings

    if profile in {"ПРОВОЛОКА", "ШЕСТИГРАННИК"}:
        if explicit_diameter is not None:
            return explicit_diameter, None, None, warnings
        if number_size is not None:
            return number_size, None, None, warnings
        if sequences:
            seq = sequences[0]
            return seq[0], seq[1] if len(seq) > 1 else None, seq[2] if len(seq) > 2 else None, warnings
        mm_match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*мм\b", text, re.I)
        if mm_match:
            return _to_decimal(mm_match.group(1)), None, None, warnings

    if profile == "ТРУБА":
        if sequences:
            seq = sequences[0]
            return seq[0], seq[1] if len(seq) > 1 else None, seq[2] if len(seq) > 2 else None, warnings
        if explicit_diameter is not None:
            return explicit_diameter, None, None, warnings

    if profile in {
        "ЛИСТ", "ПЛИТА", "ЛЕНТА", "ПОЛОСА", "КВАДРАТ", "ПОКОВКА",
        "АНОД", "ШИНА", "ПРОВОДНИК", "КОЛЬЦО",
    }:
        if sequences:
            seq = sequences[0]
            return seq[0], seq[1] if len(seq) > 1 else None, seq[2] if len(seq) > 2 else None, warnings
        if number_size is not None and profile in {"КВАДРАТ"}:
            return number_size, None, None, warnings
        if explicit_diameter is not None:
            return explicit_diameter, None, None, warnings

    if sequences:
        seq = sequences[0]
        warnings.append("Размеры разнесены по порядку исходного обозначения")
        return seq[0], seq[1] if len(seq) > 1 else None, seq[2] if len(seq) > 2 else None, warnings
    if explicit_diameter is not None:
        return explicit_diameter, None, None, warnings
    if number_size is not None:
        return number_size, None, None, warnings

    return None, None, None, warnings



def _grade_hint_present_in_description(grade_hint: str, description: str) -> bool:
    """Check whether the supplier grade column is repeated in description.

    This prevents valid dual notation such as ``Cu-ETP (М1)`` from being
    reported as a conflict while still catching ``C10200`` vs ``C11000``.
    """
    raw = normalize_space(grade_hint).upper().replace("Ё", "Е")
    description_compact = re.sub(
        r"[^0-9A-ZА-ЯЁ]+",
        "",
        normalize_space(description).upper().replace("Ё", "Е"),
    )

    without_parenthetical = re.sub(r"\([^()]*\)\s*$", "", raw).strip()
    tokens = without_parenthetical.split()
    variants = [without_parenthetical]

    if tokens:
        if tokens[0] == "ALLOY" and len(tokens) >= 2:
            variants.append(" ".join(tokens[:2]))
        elif tokens[0] not in {"ALLOY", "СПЛАВ", "СТАЛЬ"}:
            variants.append(tokens[0])

    for variant in variants:
        compact = re.sub(r"[^0-9A-ZА-ЯЁ]+", "", variant)
        if len(compact) >= 3 and compact in description_compact:
            return True
    return False

def _extract_scrap_grade(description: str) -> tuple[str, list[str], bool]:
    text = normalize_space(description)
    group_match = re.search(
        r"\b(?:ГР(?:УППА)?\.?\s*)?(Б\d{1,3})\b",
        text.upper().replace("Ё", "Е"),
    )
    if not group_match:
        grade, comments = extract_grade_from_description(description, "ЛОМ")
        return grade, comments, False

    group = group_match.group(1)
    comments = [
        f"Нормативная группа легированного лома: {group}",
        "Классификация группы: ГОСТ 2787-2024",
    ]

    material_matches = find_verified_grades(text)
    material_grades = list(dict.fromkeys(
        match.canonical for match in material_matches
        if not match.canonical.startswith("гр. ")
    ))
    if material_grades:
        comments.append(
            "Марка исходного металла, явно указанная поставщиком: "
            + ", ".join(material_grades)
        )
    else:
        explicit = re.search(
            r"\b(\d{1,3}[ХхXx][0-9А-Яа-яA-Za-z-]+)\b",
            text,
        )
        if explicit:
            comments.append(
                "Марка исходного металла, явно указанная поставщиком: "
                + explicit.group(1).upper().replace("X", "Х")
            )

    if re.search(r"\bкусок\b", text, re.I):
        comments.append("Форма лома: кусок")

    return group, comments, True



def _structured_dimension_token(token: str) -> tuple[Decimal | None, str | None]:
    text = normalize_space(token).replace(",", ".")
    if not text:
        return None, None

    cleaned = re.sub(r"^(?:Ø|[Фф]|[Дд]\s*[=:.-]?|№|N)\s*", "", text, flags=re.I)
    cleaned = re.sub(r"\s+", "", cleaned)
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None, None

    value = _to_decimal(match.group(0))
    display = None
    if (
        "-" in cleaned
        or "(" in cleaned
        or "+" in cleaned
        or re.search(r"[A-Za-zА-Яа-яЁё]", cleaned.replace("мм", ""), re.I)
    ):
        display = cleaned
    return value, display


def _structured_dimension_parts(value: object) -> list[tuple[Decimal | None, str | None]]:
    text = normalize_space(value).replace(",", ".")
    if not text:
        return []
    parts = [
        part
        for part in re.split(r"\s*[xх×*]\s*", text)
        if normalize_space(part)
    ]
    return [_structured_dimension_token(part) for part in parts]


def parse_structured_dimensions(
    profile: str,
    primary: object,
    secondary: object,
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    str | None,
    str | None,
    str | None,
]:
    """Map trusted supplier dimension columns to the assignment dimension order."""
    primary_parts = _structured_dimension_parts(primary)
    secondary_parts = _structured_dimension_parts(secondary)

    if profile in {"КРУГ", "ПРУТОК", "ПРОВОЛОКА", "ШЕСТИГРАННИК"}:
        ordered = primary_parts[:1] + secondary_parts[:1]
    elif profile == "ТРУБА":
        ordered = primary_parts[:2] + secondary_parts[:1]
    else:
        ordered = primary_parts + secondary_parts

    ordered = [part for part in ordered if part[0] is not None][:3]
    while len(ordered) < 3:
        ordered.append((None, None))

    return (
        ordered[0][0],
        ordered[1][0],
        ordered[2][0],
        ordered[0][1],
        ordered[1][1],
        ordered[2][1],
    )


def extract_material_from_description(
    description: str,
) -> tuple[str | None, str | None]:
    """Return a source-confirmed material and the exact supporting token.

    The function is deliberately conservative. It recognizes only explicit
    Russian source words and grammatical forms. It does not translate, expand
    abbreviations or infer a material from a grade.
    """

    text = normalize_space(description)
    for material, pattern in MATERIAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return material, normalize_space(match.group(0))
    return None, None


def parse_raw_item(raw: RawItem, add_full_description: bool = True) -> ParsedItem:
    profile_hint = raw.extra.get("profile_hint")
    structured_profile_missing = bool(
        raw.extra.get("structured_profile_column")
    ) and not normalize_space(profile_hint)

    inferred_profile = canonical_profile(raw.description)
    profile_restored_from_description = (
        structured_profile_missing and inferred_profile in KNOWN_PROFILES
    )
    if profile_restored_from_description:
        profile = inferred_profile
    elif structured_profile_missing:
        profile = "НЕ УКАЗАН"
    else:
        profile_source = profile_hint or raw.description
        profile = canonical_profile(str(profile_source))

    domain = classify_domain(raw.description, profile)
    warnings: list[str] = []
    review_reasons: list[str] = []
    if profile_restored_from_description:
        warnings.append(
            "Вид проката восстановлен из исходного описания: "
            + profile
        )
    elif structured_profile_missing:
        warnings.append("Вид проката в исходной колонке не указан")
        review_reasons.append("profile_unparsed")

    if profile == "ЛОМ":
        grade, grade_comments, scrap_group_found = _extract_scrap_grade(raw.description)
        if scrap_group_found:
            # v0.3.2: the group is a classification code and is stored separately
            # in attributes; it is no longer a parsing-review reason.
            pass
    elif raw.grade_hint:
        grade, grade_comments = normalize_grade(raw.grade_hint)
        description_matches = find_verified_grades(raw.description)
        description_grades = list(dict.fromkeys(
            match.canonical for match in description_matches
        ))
        hint_is_repeated = _grade_hint_present_in_description(
            raw.grade_hint,
            raw.description,
        )
        if description_grades and grade not in description_grades and not hint_is_repeated:
            warning = (
                "Конфликт марки: "
                f"колонка поставщика «{normalize_space(raw.grade_hint)}» -> {grade}; "
                f"в описании подтверждено {', '.join(description_grades)}"
            )
            warnings.append(warning)
            review_reasons.append("grade_conflict")
            grade_comments.append(warning)

            # The candidate assignment explicitly defines C17200 / Alloy 25 /
            # CuBe2 as aliases of БрБ2. When one of these aliases is present in
            # the description, keep the conflicting supplier-column value in
            # the audit comment but use the assignment canonical grade.
            if (
                "БРБ2" in description_grades
                and re.search(
                    r"(?<![0-9A-Za-zА-Яа-яЁё])"
                    r"(?:C17200|Cu\s*Be2|Alloy\s*25|БрБ2)"
                    r"(?![0-9A-Za-zА-Яа-яЁё])",
                    raw.description,
                    re.I,
                )
            ):
                grade_comments.append(
                    "Основная марка выбрана по правилу тестового задания: БРБ2"
                )
                grade_comments.append(
                    "Исходное значение колонки поставщика: "
                    + normalize_space(raw.grade_hint)
                )
                grade = "БРБ2"
    else:
        grade, grade_comments = extract_grade_from_description(raw.description, profile)

    if grade == "АВ" and re.search(r"\bТ1\b|\bT1\b", raw.description, re.I):
        grade_comments.append("Состояние поставки: Т1")

    if grade == "Х23Ю5" and re.search(r"Х23Ю5[-\s]?М\b", raw.description, re.I):
        grade_comments.append(
            "Суффикс М: исполнение ленты с нормированием механических свойств"
        )

    dim1, dim2, dim3, dim_warnings = extract_dimensions(raw.description, profile)
    extracted_dim1_display, dim2_display, dim3_display = extract_dimension_displays(
        raw.description,
        profile,
    )

    structured_dimensions = raw.extra.get("structured_dimensions")
    if isinstance(structured_dimensions, dict):
        structured = parse_structured_dimensions(
            profile,
            structured_dimensions.get("primary"),
            structured_dimensions.get("secondary"),
        )
        if any(value is not None for value in structured[:3]):
            (
                dim1,
                dim2,
                dim3,
                extracted_dim1_display,
                dim2_display,
                dim3_display,
            ) = structured
            dim_warnings = []

    price = parse_price(raw.price)
    availability = format_availability(raw.availability, raw.extra.get("availability_unit"))

    attributes: dict[str, object] = {}
    source_columns = raw.extra.get("source_columns")
    if isinstance(source_columns, dict):
        attributes["source_columns"] = source_columns

    material, material_evidence = extract_material_from_description(
        raw.description
    )
    if material:
        attributes["material"] = material
        attributes["material_evidence"] = material_evidence
    if profile_restored_from_description:
        attributes["profile_source"] = "description"
        attributes["profile_evidence"] = profile

    operator_hints: list[str] = []
    reference_status: str | None = None
    reference_research_required = False
    dim1_display: str | None = extracted_dim1_display
    dim1_unit: str | None = None
    dim1_role: str | None = None
    reference_dim1_mm: Decimal | None = None

    quantity_value, quantity_unit = parse_quantity(availability)

    if profile in {"ПРУТОК", "КРУГ"} and dim1 is not None:
        dim1_role = "DIAMETER"
        dim1_unit = "MM"
        if dim2 is not None:
            attributes["dimension_completeness"] = "DIAMETER_AND_LENGTH"
            attributes["round_bar_length_mm"] = format(dim2, "f")
        else:
            attributes["dimension_completeness"] = "DIAMETER_ONLY_SOURCE"

    inch = inch_fraction(raw.description) if profile == "ТРУБА" else None
    if inch:
        dim1_display, dim1, reference_dim1_mm = inch
        dim1_unit = "INCH"
        dim1_role = "OUTER_DIAMETER"
        attributes["coil_length_m"] = (
            re.search(r"бухтах?\s+по\s+(\d+(?:[.,]\d+)?)\s*метр", raw.description, re.I).group(1)
            if re.search(r"бухтах?\s+по\s+(\d+(?:[.,]\d+)?)\s*метр", raw.description, re.I)
            else None
        )
        attributes["reference_mm"] = format(reference_dim1_mm, "f")

    if profile == "ЛОМ":
        attributes.update(scrap_attributes(raw.description))

    if profile == "СЕТКА":
        attributes.update(mesh_attributes(raw.description))
        if attributes.get("mesh_designation") and grade == "предпол.":
            grade = "НЕ УКАЗАНА"
            grade_comments.append("Марка материала поставщиком не указана")
        if dim1 is not None:
            dim1_role = "WIDTH"
            dim1_unit = "MM"

    hint = reference_hint_for(grade)
    if hint:
        reference_status = hint.status.value
        reference_research_required = hint.status.value != "CONFIRMED_NTD_ALIAS"
        operator_hints.append(hint.operator_message)
        attributes["reference_source"] = hint.source_reference
        if hint.suggested_designation:
            attributes["suggested_designation"] = hint.suggested_designation

    comments: list[str] = []
    comments.extend(grade_comments)
    comments.extend(raw.extra.get("comments", []))
    comments.extend(dim_warnings)
    if material and grade in {"предпол.", "НЕ УКАЗАНА"}:
        comments.append(
            "Материал явно указан в источнике: "
            + material
        )

    for dim_warning in dim_warnings:
        if dim_warning.startswith("Требуется решение заказчика:"):
            warnings.append(dim_warning)
            review_reasons.append("business_rule_pending")
        elif dim_warning.startswith("Требуется проверка:"):
            warnings.append(dim_warning)
            review_reasons.append("multiple_dimension_sets")

    if raw.extra.get("nds_unknown"):
        comments.append("НДС не указан")
    if add_full_description:
        comments.append(f"Исходное описание: {normalize_space(raw.description)}")

    if grade == "предпол.":
        warnings.append("Марка не указана или не распознана")
        review_reasons.append("unconfirmed_grade")
    elif grade == "НЕ УКАЗАНА" and profile == "СЕТКА":
        # For ГОСТ 3187 designations P32/P48 the material is not determined
        # from the mesh code. This is valid missing source data, not a parser error.
        pass

    dimension_required = profile not in DIMENSION_OPTIONAL_PROFILES
    if dim1 is None and dimension_required:
        warnings.append("Размер не распознан")
        comments.append("Размер не указан или требует проверки")
        review_reasons.append("dimension_unparsed")

    confidence = 1.0
    if grade == "предпол.":
        confidence -= 0.25
    if dim1 is None and dimension_required:
        confidence -= 0.25
    if any(value.startswith("Конфликт марки:") for value in warnings):
        confidence -= 0.35
    if "unverified_designation" in review_reasons:
        confidence -= 0.20
    if "business_rule_pending" in review_reasons:
        confidence -= 0.15
    confidence -= min(0.2, 0.05 * len(dim_warnings))

    review_reasons = _dedupe(review_reasons)
    return ParsedItem(
        supplier=raw.supplier,
        profile=profile,
        grade=grade,
        dim1=dim1,
        dim2=dim2,
        dim3=dim3,
        availability=availability,
        price_rub_kg=price,
        comment="; ".join(_dedupe(comments)),
        source=raw.source,
        raw_description=normalize_space(raw.description),
        confidence=max(0.0, confidence),
        warnings=_dedupe(warnings),
        domain=domain,
        requires_review=bool(review_reasons),
        review_reasons=review_reasons,
        display_name=build_display_name(profile, grade, attributes, dim1_display),
        quantity_value=quantity_value,
        quantity_unit=quantity_unit,
        dim1_display=dim1_display,
        dim2_display=dim2_display,
        dim3_display=dim3_display,
        dim1_unit=dim1_unit,
        dim1_role=dim1_role,
        reference_dim1_mm=reference_dim1_mm,
        reference_status=reference_status,
        reference_research_required=reference_research_required,
        operator_hints=_dedupe(operator_hints),
        attributes=attributes,
    )


def _number_text(value: int | float | Decimal) -> str:
    dec = Decimal(str(value))
    if dec == dec.to_integral_value():
        return str(int(dec))
    return format(dec.normalize(), "f")


def _to_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = normalize_space(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
