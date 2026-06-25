from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .base import LLMResult


class ReplayLLMProvider:
    """Offline provider for deterministic development against recorded fixtures."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self.fixtures_dir = Path(fixtures_dir)

    def parse(self, payload: dict[str, Any]) -> LLMResult:
        key = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        path = self.fixtures_dir / f"{key}.json"
        if not path.exists():
            raise FileNotFoundError(f"Нет replay-фикстуры для запроса: {key}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LLMResult(
            data=raw["data"],
            input_tokens=int(raw.get("input_tokens", 0)),
            output_tokens=int(raw.get("output_tokens", 0)),
            model=str(raw.get("model", "replay")),
        )
