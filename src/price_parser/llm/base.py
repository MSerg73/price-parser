from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class LLMResult:
    data: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMProvider(Protocol):
    def parse(self, payload: dict[str, Any]) -> LLMResult:
        """Return structured data for an ambiguous row."""
        ...
