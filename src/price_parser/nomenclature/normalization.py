from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from price_parser.normalization import canonical_profile, grade_match_key, normalize_space

from .errors import SearchValidationError


_DIMENSION_SEPARATOR = re.compile(r"\s*(?:[xх×*])\s*", re.IGNORECASE)


def normalize_profile(value: str) -> str:
    text = normalize_space(value)
    if not text:
        raise SearchValidationError("Профиль не указан")
    profile = canonical_profile(text)
    if profile == "НЕ ОПРЕДЕЛЁН":
        raise SearchValidationError(f"Не удалось определить профиль: {value!r}")
    return profile


def normalize_grade_key(value: str) -> str:
    text = normalize_space(value)
    if not text:
        raise SearchValidationError("Марка не указана")
    return grade_match_key(text)


def normalize_dimension(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    text = unicodedata.normalize("NFKC", normalize_space(value))
    text = text.replace(",", ".")
    text = re.sub(r"(?i)\s*(?:мм|mm|in|дюйм|\")\s*$", "", text)
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if fraction:
        denominator = Decimal(fraction.group(2))
        if denominator == 0:
            raise SearchValidationError("Знаменатель размера не может быть нулём")
        result = Decimal(fraction.group(1)) / denominator
        if result <= 0:
            raise SearchValidationError("Размер должен быть больше нуля")
        return result.normalize()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise SearchValidationError(f"Некорректный размер: {value!r}")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise SearchValidationError(f"Некорректный размер: {value!r}") from exc
    if result <= 0:
        raise SearchValidationError("Размер должен быть больше нуля")
    return result.normalize()


def parse_dimensions(
    value: str | list[object] | tuple[object, ...] | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if value is None or value == "":
        return (None, None, None)

    if isinstance(value, str):
        parts = _DIMENSION_SEPARATOR.split(value.strip())
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise SearchValidationError("Размеры должны быть строкой или массивом")

    if len(parts) > 3:
        raise SearchValidationError("Поддерживается не более трёх компонентов размера")

    parsed = [normalize_dimension(part) for part in parts]
    parsed.extend([None] * (3 - len(parsed)))
    return tuple(parsed)  # type: ignore[return-value]


def dimensions_equal(
    left: tuple[Decimal | None, Decimal | None, Decimal | None],
    right: tuple[Decimal | None, Decimal | None, Decimal | None],
) -> bool:
    for expected, actual in zip(left, right, strict=True):
        if expected is None:
            continue
        if actual is None or expected != actual:
            return False
    return True


def dimension_deltas(
    query: tuple[Decimal | None, Decimal | None, Decimal | None],
    item: tuple[Decimal | None, Decimal | None, Decimal | None],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    values: list[Decimal | None] = []
    for expected, actual in zip(query, item, strict=True):
        if expected is None:
            values.append(None)
        elif actual is None:
            values.append(None)
        else:
            values.append(abs(expected - actual))
    return tuple(values)  # type: ignore[return-value]
