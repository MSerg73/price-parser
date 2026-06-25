from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from price_parser.llm.pilot_provider import ReplayPilotProvider, _payload_hash


def payload() -> dict:
    return {
        "rows": [
            {
                "source_id": "file.xls / Sheet1 / строка 1",
                "supplier": "Test",
                "description": "Труба 12Х18Н10Т 5х1,5",
                "current_profile": "ТРУБА",
                "current_grade": "12Х18Н10Т",
                "current_dimensions": ["5", "1.5", None],
                "domain_policy": {
                    "domain": "METAL_PRODUCT",
                    "preferred_sources": ["исходная строка"],
                    "forbidden_inferences": ["не выдумывать"],
                },
                "requires_review": False,
                "review_reasons": [],
            }
        ]
    }


def valid_row() -> dict:
    return {
        "source_id": "file.xls / Sheet1 / строка 1",
        "decision": "KEEP",
        "is_product": True,
        "profile": "ТРУБА",
        "grade": "12Х18Н10Т",
        "dim1": "5",
        "dim2": "1.5",
        "dim3": None,
        "additional_info": [],
        "warnings": [],
        "confidence": 0.99,
        "evidence_basis": "SOURCE_TEXT",
        "research_required": False,
        "research_queries": [],
    }


def write_fixture(directory: Path, body: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{_payload_hash(payload())}.json").write_text(
        json.dumps(body, ensure_ascii=False),
        encoding="utf-8",
    )


def test_replay_provider_validates_schema(tmp_path: Path) -> None:
    row = valid_row()
    del row["confidence"]
    write_fixture(
        tmp_path,
        {
            "rows": [row],
            "input_tokens": 10,
            "output_tokens": 5,
            "model": "replay",
        },
    )
    with pytest.raises(ValidationError):
        ReplayPilotProvider(tmp_path).parse(payload())


def test_replay_provider_rejects_negative_tokens(tmp_path: Path) -> None:
    write_fixture(
        tmp_path,
        {
            "rows": [valid_row()],
            "input_tokens": -1,
            "output_tokens": 5,
            "model": "replay",
        },
    )
    with pytest.raises(ValueError, match="отрицательное"):
        ReplayPilotProvider(tmp_path).parse(payload())
