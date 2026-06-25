from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Iterable, Sequence

from .models import ParsedItem
from .normalization import (
    canonical_profile,
    grade_match_key,
    normalize_grade,
    normalize_space,
    search_profile_variants,
)


_NUMBER = r"\d+(?:[.,]\d+)?"
_MULTI_DIMENSION_RE = re.compile(
    rf"(?<!\d)"
    rf"(?P<d1>{_NUMBER})\s*[xх×*]\s*(?P<d2>{_NUMBER})"
    rf"(?:\s*[xх×*]\s*(?P<d3>{_NUMBER}))?"
    rf"(?!\d)",
    re.IGNORECASE,
)
_EXPLICIT_DIMENSION_RE = re.compile(
    rf"(?P<marker>Ø|ø|⌀|[Фф]|[Дд](?:иаметр)?\s*[=:.-]?|№|#|[Nn](?:[oOº°])?(?=\s*\d))"
    rf"\s*(?P<d1>{_NUMBER})",
    re.IGNORECASE,
)
_UNIT_DIMENSION_RE = re.compile(
    rf"(?<![0-9A-Za-zА-Яа-яЁё])(?P<d1>{_NUMBER})\s*"
    rf"(?P<unit>мм|миллиметр(?:а|ов)?|см|сантиметр(?:а|ов)?)\b",
    re.IGNORECASE,
)
_STANDALONE_NUMBER_RE = re.compile(
    rf"(?<![0-9A-Za-zА-Яа-яЁё])(?P<number>{_NUMBER})"
    rf"(?![0-9A-Za-zА-Яа-яЁё])"
)
_EXPLICIT_GRADE_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])"
    r"(?:сталь\.?\s+|ст\.\s*|ст\s+|ст(?=\d)|марка\s+)"
    r"(?P<grade>[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._/-]*)",
    re.IGNORECASE,
)
_ASSIGNMENT_GRADE_ALIAS_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])"
    r"(?P<grade>БрБ2|C17200|CuBe2|Alloy\s*25)"
    r"(?![0-9A-Za-zА-Яа-яЁё])",
    re.IGNORECASE,
)
_PACKAGING_PAREN_RE = re.compile(
    r"\([^)]*(?:кг|шт|упак|короб|пачк|бухт|катуш|рулон)[^)]*\)",
    re.IGNORECASE,
)
_NON_DIMENSION_UNIT_AFTER_RE = re.compile(
    r"^\s*(?:кг|г|т|шт|руб|₽|%|проц|лет|год)\b",
    re.IGNORECASE,
)
_STANDARD_PREFIX_RE = re.compile(
    r"(?:ГОСТ|ОСТ|ТУ|DIN|ASTM|EN|AISI)\s*$",
    re.IGNORECASE,
)
_TEXT_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-_/][0-9A-Za-zА-Яа-яЁё]+)*")
_NAME_STOPWORDS = {
    "И",
    "ИЛИ",
    "ИЗ",
    "В",
    "ВО",
    "НА",
    "ПО",
    "ДЛЯ",
    "С",
    "СО",
    "ПОД",
    "ПРИ",
    "ММ",
    "СМ",
    "М",
}
_EMPTY_GRADES = {"", "?", "ПРЕДПОЛ.", "ПРЕДПОЛ", "НЕ УКАЗАН", "НЕ ОПРЕДЕЛЁН"}


@dataclass(frozen=True, slots=True)
class SearchQuery:
    raw: str
    normalized: str
    name: str | None
    grade: str | None
    dimensions: tuple[Decimal, ...]
    profile: str | None
    accepted_profiles: frozenset[str]
    name_terms: tuple[str, ...]

    @property
    def dim1(self) -> Decimal | None:
        return self.dimensions[0] if self.dimensions else None

    @property
    def dim2(self) -> Decimal | None:
        return self.dimensions[1] if len(self.dimensions) > 1 else None

    @property
    def dim3(self) -> Decimal | None:
        return self.dimensions[2] if len(self.dimensions) > 2 else None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "profile": self.profile,
            "grade": self.grade,
            "dim1": _decimal_json(self.dim1),
            "dim2": _decimal_json(self.dim2),
            "dim3": _decimal_json(self.dim3),
            "name_terms": list(self.name_terms),
            "accepted_profiles": sorted(self.accepted_profiles),
        }


@dataclass(slots=True)
class SearchResult:
    item: ParsedItem
    match_type: str
    size_delta: Decimal
    display_profile: str | None = None
    is_profile_alias: bool = False
    dimension_deltas: tuple[Decimal, ...] = ()

    @property
    def effective_profile(self) -> str:
        """Profile shown in the separate search view."""
        return self.display_profile or self.item.profile


@dataclass(frozen=True, slots=True)
class _GradeMatch:
    grade: str
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _DimensionMatch:
    values: tuple[Decimal, ...]
    span: tuple[int, int]


def parse_search_query(
    query: str,
    items: Sequence[ParsedItem] | None = None,
) -> SearchQuery:
    """Parse a free-form search mask into optional fields.

    Supported masks are intentionally data-driven rather than tied to a fixed
    list of products. A request may contain any combination of:

    * product name/profile;
    * grade;
    * one, two or three dimensions.

    Missing fields are wildcards and never raise an exception. An unknown name
    is valid and simply produces an empty result set.
    """

    raw = str(query or "")
    normalized = _normalize_query_text(raw)
    if not normalized:
        return SearchQuery(
            raw=raw,
            normalized="",
            name=None,
            grade=None,
            dimensions=(),
            profile=None,
            accepted_profiles=frozenset(),
            name_terms=(),
        )

    grade_match = _extract_query_grade(normalized, items or ())
    without_grade = _blank_spans(
        normalized,
        [grade_match.span] if grade_match is not None else [],
    )

    dimension_match = _extract_query_dimensions(without_grade)
    working = _blank_spans(
        without_grade,
        [dimension_match.span] if dimension_match is not None else [],
    )

    name = _clean_name_text(working)
    actual_profiles = {
        canonical_profile(str(item.profile or ""))
        for item in (items or ())
        if str(item.profile or "").strip()
    }

    profile: str | None = None
    accepted_profiles: frozenset[str] = frozenset()
    if name:
        candidate_profile = canonical_profile(name)
        candidate_variants = search_profile_variants(candidate_profile)
        if actual_profiles and candidate_variants.intersection(actual_profiles):
            profile = candidate_profile
            accepted_profiles = candidate_variants
        elif not actual_profiles and candidate_profile != "НЕ ОПРЕДЕЛЁН":
            # Useful for direct parser tests without an inventory.
            profile = candidate_profile
            accepted_profiles = candidate_variants

    name_terms = _name_terms(name, profile)
    return SearchQuery(
        raw=raw,
        normalized=normalized,
        name=name or None,
        grade=grade_match.grade if grade_match is not None else None,
        dimensions=dimension_match.values if dimension_match is not None else (),
        profile=profile,
        accepted_profiles=accepted_profiles,
        name_terms=name_terms,
    )


def search_items(
    items: list[ParsedItem],
    query: str,
    *,
    parsed_query: SearchQuery | None = None,
) -> list[SearchResult]:
    """Search by any supplied subset of name, grade and dimensions.

    No field is mandatory. Missing query fields act as wildcards. Unknown text
    and absent matches return an empty list instead of raising an exception.
    """

    parsed = parsed_query or parse_search_query(query, items)
    if not parsed.normalized:
        return []

    candidates: list[SearchResult] = []
    for item in items:
        if not _item_matches_name(item, parsed):
            continue
        if (
            parsed.grade is not None
            and grade_match_key(item.grade) != grade_match_key(parsed.grade)
        ):
            continue

        dimension_deltas = _dimension_deltas(item, parsed.dimensions)
        if dimension_deltas is None:
            # The query is valid, but this item cannot satisfy every explicitly
            # supplied dimension because the source row lacks one of them.
            continue

        size_delta = sum(dimension_deltas, Decimal("0"))
        exact = all(delta == 0 for delta in dimension_deltas)
        candidates.append(
            SearchResult(
                item=item,
                match_type=(
                    "ТОЧНОЕ"
                    if exact or not parsed.dimensions
                    else "БЛИЖАЙШИЙ РАЗМЕР"
                ),
                size_delta=size_delta,
                dimension_deltas=dimension_deltas,
            )
        )

    return sorted(
        candidates,
        key=lambda result: (
            0 if result.match_type == "ТОЧНОЕ" else 1,
            result.size_delta,
            max(result.dimension_deltas, default=Decimal("0")),
            result.dimension_deltas,
            _profile_sort_penalty(result.item, parsed),
            str(result.item.supplier or ""),
            getattr(result.item.source, "row", 0),
        ),
    )


def expand_round_bar_search_view(
    results: list[SearchResult],
) -> list[SearchResult]:
    """Build the assignment search view with both ПРУТОК and КРУГ labels.

    The customer-approved assumption treats round bar labels as equivalent in
    the *separate search artifact only*. Source items remain unchanged in the
    canonical ten-column result. Each source match is therefore represented
    twice in the search view: once with its source profile and once with the
    equivalent display profile. Alias rows are explicitly marked so they cannot
    be mistaken for an additional supplier stock position.
    """
    expanded: list[SearchResult] = []
    aliases = {"ПРУТОК": "КРУГ", "КРУГ": "ПРУТОК"}

    for result in results:
        source_profile = canonical_profile(result.item.profile)
        expanded.append(
            replace(
                result,
                display_profile=source_profile,
                is_profile_alias=False,
            )
        )

        alias = aliases.get(source_profile)
        if alias is not None:
            expanded.append(
                replace(
                    result,
                    display_profile=alias,
                    is_profile_alias=True,
                )
            )

    return expanded


def _extract_query_grade(
    query: str,
    items: Iterable[ParsedItem],
) -> _GradeMatch | None:
    explicit = _EXPLICIT_GRADE_RE.search(query)
    if explicit:
        normalized, _ = normalize_grade(explicit.group("grade"))
        return _GradeMatch(normalized, explicit.span())

    alias = _ASSIGNMENT_GRADE_ALIAS_RE.search(query)
    if alias:
        normalized, _ = normalize_grade(alias.group("grade"))
        return _GradeMatch(normalized, alias.span("grade"))

    candidates: dict[str, str] = {}
    for item in items:
        value = normalize_space(getattr(item, "grade", ""))
        if value.upper() in _EMPTY_GRADES:
            continue
        key = grade_match_key(value)
        if not key or key.isdigit():
            # A bare numeric grade is ambiguous with a single size. It remains
            # searchable through an explicit marker: ``ст.20``/``марка 20``.
            continue
        candidates.setdefault(key, value)

    for value in sorted(candidates.values(), key=_grade_sort_key, reverse=True):
        match = _grade_pattern(value).search(query)
        if match:
            normalized, _ = normalize_grade(value)
            return _GradeMatch(normalized, match.span())

    return None


def _extract_query_dimensions(query_without_grade: str) -> _DimensionMatch | None:
    # Explicit multi-dimensional blocks have priority.
    multi = _MULTI_DIMENSION_RE.search(query_without_grade)
    if multi:
        values = tuple(
            _decimal(multi.group(name))
            for name in ("d1", "d2", "d3")
            if multi.group(name) is not None
        )
        return _DimensionMatch(values[:3], multi.span())

    explicit = _EXPLICIT_DIMENSION_RE.search(query_without_grade)
    if explicit:
        return _DimensionMatch(
            (_decimal(explicit.group("d1")),),
            explicit.span(),
        )

    with_unit = _UNIT_DIMENSION_RE.search(query_without_grade)
    if with_unit:
        return _DimensionMatch(
            (_decimal(with_unit.group("d1")),),
            with_unit.span(),
        )

    # A single unmarked number means Dimension 1 for any product profile.
    # When several unrelated numbers remain, the rightmost valid one is used;
    # explicit x/х/×/* separators are required to declare Dimension 2/3.
    valid: list[re.Match[str]] = []
    for match in _STANDALONE_NUMBER_RE.finditer(query_without_grade):
        prefix = query_without_grade[: match.start()]
        suffix = query_without_grade[match.end() :]
        if _STANDARD_PREFIX_RE.search(prefix):
            continue
        if _NON_DIMENSION_UNIT_AFTER_RE.search(suffix):
            continue
        valid.append(match)

    if not valid:
        return None
    selected = valid[-1]
    return _DimensionMatch(
        (_decimal(selected.group("number")),),
        selected.span(),
    )


def _item_matches_name(item: ParsedItem, parsed: SearchQuery) -> bool:
    if not parsed.name:
        return True

    item_profile = canonical_profile(str(item.profile or ""))
    if parsed.profile is not None and item_profile not in parsed.accepted_profiles:
        return False

    if not parsed.name_terms:
        return parsed.profile is not None

    searchable = " ".join(
        str(value or "")
        for value in (
            getattr(item, "profile", ""),
            (getattr(item, "attributes", {}) or {}).get("material", ""),
            getattr(item, "raw_description", ""),
            getattr(item, "comment", ""),
        )
    )
    item_tokens = _text_tokens(searchable)
    return all(_term_present(term, item_tokens) for term in parsed.name_terms)


def _dimension_deltas(
    item: ParsedItem,
    dimensions: tuple[Decimal, ...],
) -> tuple[Decimal, ...] | None:
    if not dimensions:
        return ()

    item_dimensions = (
        getattr(item, "dim1", None),
        getattr(item, "dim2", None),
        getattr(item, "dim3", None),
    )
    deltas: list[Decimal] = []
    for index, expected in enumerate(dimensions):
        actual = item_dimensions[index]
        if actual is None:
            return None
        deltas.append(abs(Decimal(actual) - expected))
    return tuple(deltas)


def _profile_sort_penalty(item: ParsedItem, parsed: SearchQuery) -> int:
    if parsed.profile is None:
        return 0
    return 0 if canonical_profile(str(item.profile or "")) == parsed.profile else 1


def _clean_name_text(value: str) -> str:
    text = _PACKAGING_PAREN_RE.sub(" ", value)
    text = re.sub(r"(?<!\w)(?:Ø|ø|⌀|[Фф]|№|#|[Nn](?:[oOº°])?)(?!\w)", " ", text)
    text = re.sub(r"(?<!\w)(?:ст(?:аль)?\.?|марка)(?!\w)", " ", text, flags=re.I)
    text = re.sub(r"(?<!\w)(?:мм|см|миллиметр(?:а|ов)?|сантиметр(?:а|ов)?)(?!\w)", " ", text, flags=re.I)
    text = re.sub(r"[()[\]{},;:=]+", " ", text)
    return normalize_space(text).strip("-/.")


def _name_terms(name: str, profile: str | None) -> tuple[str, ...]:
    if not name:
        return ()
    terms: list[str] = []
    for token in sorted(_text_tokens(name)):
        if token in _NAME_STOPWORDS or token.isdigit():
            continue
        if profile is not None and _token_is_profile(token, profile):
            continue
        terms.append(token)
    return tuple(dict.fromkeys(terms))


def _token_is_profile(token: str, profile: str) -> bool:
    token_key = _token_key(token)
    profile_key = _token_key(profile)
    if not token_key or not profile_key:
        return False
    if token_key == profile_key:
        return True
    if min(len(token_key), len(profile_key)) >= 4:
        return token_key.startswith(profile_key) or profile_key.startswith(token_key)
    return canonical_profile(token) == profile


def _term_present(term: str, item_tokens: set[str]) -> bool:
    if term in item_tokens:
        return True
    if len(term) < 4:
        return False
    return any(
        len(candidate) >= 4
        and (candidate.startswith(term) or term.startswith(candidate))
        for candidate in item_tokens
    )


def _text_tokens(value: str) -> set[str]:
    return {
        _token_key(match.group(0))
        for match in _TEXT_TOKEN_RE.finditer(
            unicodedata.normalize("NFKC", str(value or ""))
        )
        if _token_key(match.group(0))
    }


def _token_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).upper().replace("Ё", "Е")


def _normalize_query_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\u2007", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def _blank_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(max(start, 0), min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)


def _grade_pattern(value: str) -> re.Pattern[str]:
    chunks = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+|[^0-9A-Za-zА-Яа-яЁё]+", value)
    parts: list[str] = []
    for chunk in chunks:
        if chunk[0].isalnum():
            parts.append(re.escape(chunk))
        else:
            parts.append(r"[\s_./\-–—]*")
    body = "".join(parts)
    return re.compile(
        rf"(?<![0-9A-Za-zА-Яа-яЁё])({body})(?![0-9A-Za-zА-Яа-яЁё])",
        re.IGNORECASE,
    )


def _grade_sort_key(value: str) -> tuple[int, int]:
    alnum = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", value)
    return (len(alnum), len(value))


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", "."))


def _decimal_json(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)
