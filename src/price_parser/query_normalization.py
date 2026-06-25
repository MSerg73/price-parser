from __future__ import annotations

import re
import unicodedata


def normalize_search_query(raw_query: str | None) -> str:
    """Normalize only formatting, never the business meaning of a query.

    Whitespace differences, tabs and non-breaking spaces must not affect
    parsing. Dimension separators are intentionally preserved here because
    Cyrillic ``Х`` can be part of a steel grade (for example ``12Х18Н10Т``).
    The inventory-aware parser in :mod:`price_parser.search` decides whether a
    particular ``x/х/×/*`` belongs to a dimension block.
    """

    text = unicodedata.normalize("NFKC", str(raw_query or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\u2007", " ")
    )
    return re.sub(r"\s+", " ", text).strip()
